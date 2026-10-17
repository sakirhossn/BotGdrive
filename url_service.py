"""URL Inspector, SSRF-safe Stream Downloader, and Provider Integration.

Supports direct downloadable links, Dropbox shared links, Google Drive public files,
and GitHub release assets. Zero external dependencies (Python standard library only).
"""

from __future__ import annotations

import http.client
import ipaddress
import logging
import mimetypes
import os
from pathlib import Path
import re
import socket
import time
from typing import Any, Callable, Dict, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request

import config
import utils

logger = logging.getLogger(__name__)

# Max file size: 2,000 MB (2 GB)
MAX_DOWNLOAD_BYTES: int = 2000 * 1024 * 1024

# Pending confirmation tasks cache: {task_id: {"url": str, "name": str, "size": int, "mime": str, "target_id": str, "target_name": str, "created_at": float}}
_PENDING_TASKS: Dict[str, Dict[str, Any]] = {}
PENDING_TASK_TTL: float = 900.0  # 15 minutes


class SSRFSecurityError(PermissionError):
    """Raised when a URL attempts to connect to private, loopback, or cloud metadata IP."""
    pass


def is_ip_allowed(ip_str: str) -> bool:
    """Check if an IP address is a safe public address (not loopback, private, or metadata)."""
    try:
        ip = ipaddress.ip_address(ip_str)
        # Block private networks, loopback, link-local, multicast, reserved
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
        # Specific cloud metadata addresses
        if ip_str in ("169.254.169.254", "169.254.169.250", "metadata.google.internal"):
            return False
        return True
    except ValueError:
        return False


def validate_url_security(url: str) -> str:
    """Validate that the URL uses http/https and does not resolve to private or internal addresses."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise SSRFSecurityError(f"Unsupported URL scheme '{parsed.scheme}'. Only http and https are allowed.")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFSecurityError("Invalid URL: missing hostname.")

    # Block localhost literals immediately
    if hostname.lower() in ("localhost", "127.0.0.1", "::1", "metadata.google.internal"):
        raise SSRFSecurityError(f"Access to '{hostname}' is restricted for security.")

    try:
        # Resolve hostname to all IP addresses
        addr_info = socket.getaddrinfo(hostname, None)
        for family, socktype, proto, canonname, sockaddr in addr_info:
            ip_str = sockaddr[0]
            if not is_ip_allowed(ip_str):
                raise SSRFSecurityError(f"Security Alert: Hostname '{hostname}' resolves to private/restricted IP ({ip_str}).")
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve host '{hostname}': {exc}")

    return url


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """HTTP redirect handler that enforces SSRF checks on every redirect destination."""

    def __init__(self, max_redirects: int = 5) -> None:
        self.max_redirects = max_redirects
        self.redirect_count = 0

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request:
        self.redirect_count += 1
        if self.redirect_count > self.max_redirects:
            raise urllib.error.HTTPError(req.full_url, code, "Too many redirects", headers, fp)

        # Validate security of the redirect target
        validate_url_security(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_safe_opener() -> urllib.request.OpenerDirector:
    """Construct a urllib opener with custom SSRF-safe redirect handling."""
    return urllib.request.build_opener(SafeRedirectHandler())


def normalize_provider_url(raw_url: str) -> Tuple[str, Optional[str]]:
    """Transform known public hosting links to direct downloadable streams.

    Returns:
        (transformed_url, provider_name)
    """
    parsed = urllib.parse.urlsplit(raw_url)
    hostname = (parsed.hostname or "").lower()

    # 1. Dropbox
    if "dropbox.com" in hostname:
        # Change dl=0 to dl=1
        query = urllib.parse.parse_qs(parsed.query)
        query["dl"] = ["1"]
        new_query = urllib.parse.urlencode(query, doseq=True)
        transformed = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment)
        )
        return transformed, "Dropbox"

    # 2. Google Drive Shared Link
    if "drive.google.com" in hostname or "docs.google.com" in hostname:
        file_id = None
        # /file/d/{id}/view
        m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", parsed.path)
        if m:
            file_id = m.group(1)
        elif "id" in urllib.parse.parse_qs(parsed.query):
            file_id = urllib.parse.parse_qs(parsed.query)["id"][0]

        if file_id:
            direct_url = f"https://drive.google.com/uc?export=download&id={file_id}"
            return direct_url, "Google Drive (Public Share)"

    # 3. GitHub Releases / Raw
    if "github.com" in hostname and "/releases/download/" in parsed.path:
        return raw_url, "GitHub Release Asset"
    if "raw.githubusercontent.com" in hostname:
        return raw_url, "GitHub Raw"

    return raw_url, None


def extract_filename_from_headers(headers: http.client.HTTPMessage, fallback_url: str) -> str:
    """Extract filename from Content-Disposition header, with fallback to URL path or timestamp."""
    disp = headers.get("Content-Disposition", "")
    if disp:
        m = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)", disp) or re.search(r'filename\s*=\s*"?([^";]+)"?', disp)
        if m:
            clean = urllib.parse.unquote(m.group(1).strip('"\' '))
            if clean:
                return clean

    parsed = urllib.parse.urlsplit(fallback_url)
    path_name = Path(urllib.parse.unquote(parsed.path)).name
    if path_name and "." in path_name:
        return path_name

    content_type = (headers.get("Content-Type", "")).split(";")[0].strip().lower()
    ext = mimetypes.guess_extension(content_type) or ".bin"
    return f"file_{int(time.time())}{'.jpg' if ext == '.jpe' else ext}"


def probe_url(url: str, timeout: float = 12.0) -> Dict[str, Any]:
    """Inspect a URL and extract metadata (filename, size, MIME type, direct status).

    Raises SSRFSecurityError if URL points to internal network.
    """
    target_url, provider = normalize_provider_url(url)
    validate_url_security(target_url)

    req = urllib.request.Request(
        target_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
        },
        method="HEAD",
    )

    opener = build_safe_opener()
    resp = None
    headers: Optional[http.client.HTTPMessage] = None
    final_url = target_url

    try:
        resp = opener.open(req, timeout=timeout)
        headers = resp.headers
        final_url = resp.geturl()
    except urllib.error.HTTPError as exc:
        # Some servers reject HEAD with 405 Method Not Allowed or 403. Fallback to GET with Range 0-0.
        if exc.code in (405, 501, 403):
            get_req = urllib.request.Request(
                target_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Range": "bytes=0-0",
                    "Accept": "*/*",
                },
            )
            try:
                resp = opener.open(get_req, timeout=timeout)
                headers = resp.headers
                final_url = resp.geturl()
            except Exception as get_exc:
                logger.warning("GET fallback probe failed for %s: %s", target_url, get_exc)
                headers = exc.headers
        else:
            headers = exc.headers
    except Exception as exc:
        logger.warning("HEAD probe error for %s: %s", target_url, exc)
        # Attempt minimal GET stream probe
        get_req = urllib.request.Request(
            target_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Range": "bytes=0-1024",
                "Accept": "*/*",
            },
        )
        resp = opener.open(get_req, timeout=timeout)
        headers = resp.headers
        final_url = resp.geturl()
    finally:
        if resp:
            try:
                resp.close()
            except Exception:
                pass

    if not headers:
        raise ValueError("Could not retrieve response headers from server.")

    content_type_raw = headers.get("Content-Type", "application/octet-stream")
    content_type = content_type_raw.split(";")[0].strip().lower()

    # Content length
    content_length_str = headers.get("Content-Length")
    size_bytes = int(content_length_str) if content_length_str and content_length_str.isdigit() else 0

    # Detect HTML preview pages
    is_html_webpage = content_type in ("text/html", "application/xhtml+xml")

    # Filename
    filename = extract_filename_from_headers(headers, final_url)
    safe_name = utils.sanitize_filename(filename)

    parsed_final = urllib.parse.urlsplit(final_url)
    host = parsed_final.hostname or "remote-server"

    return {
        "url": target_url,
        "final_url": final_url,
        "provider": provider,
        "filename": safe_name,
        "size_bytes": size_bytes,
        "mime_type": content_type,
        "is_html": is_html_webpage,
        "host": host,
    }


def stream_url_to_file(
    url: str,
    destination_path: Path | str,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    status_callback: Optional[Callable[[str], None]] = None,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    timeout: float = 30.0,
    max_retries: int = 5,
) -> Tuple[Path, int, str]:
    """Stream a remote URL directly to a local file with live progress, auto-resume via HTTP Range, and 2 GB limit.

    Returns:
        (dest_path, actual_bytes_downloaded, mime_type)
    """
    target_url, _ = normalize_provider_url(url)
    validate_url_security(target_url)

    dest = Path(destination_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    opener = build_safe_opener()
    chunk_size = 256 * 1024  # 256 KB streaming chunks
    downloaded_bytes = 0
    total_size = 0
    mime_type = "application/octet-stream"

    retry_count = 0
    while True:
        try:
            req_headers: Dict[str, str] = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "*/*",
            }

            if downloaded_bytes > 0:
                req_headers["Range"] = f"bytes={downloaded_bytes}-"
                file_mode = "ab"
                if status_callback:
                    status_callback(f"🔄 Reconnecting and resuming from {utils.format_file_size(downloaded_bytes)}...")
                logger.info("Resuming stream for '%s' from byte %d", dest.name, downloaded_bytes)
            else:
                file_mode = "wb"

            req = urllib.request.Request(target_url, headers=req_headers)

            with opener.open(req, timeout=timeout) as resp:
                validate_url_security(resp.geturl())

                # On initial connection, inspect headers and disk space
                if downloaded_bytes == 0:
                    content_length_str = resp.headers.get("Content-Length")
                    total_size = int(content_length_str) if content_length_str and content_length_str.isdigit() else 0
                    content_type_raw = resp.headers.get("Content-Type", "")
                    mime_type = content_type_raw.split(";")[0].strip().lower() or "application/octet-stream"

                    if total_size > max_bytes:
                        raise ValueError(f"File size ({utils.format_file_size(total_size)}) exceeds limit of {utils.format_file_size(max_bytes)}.")

                    if total_size > 0:
                        is_ok, free_bytes, needed_bytes = utils.check_disk_space(total_size, dest.parent)
                        if not is_ok:
                            raise OSError(
                                f"Insufficient server storage space (available: {utils.format_file_size(free_bytes)}, "
                                f"needed: {utils.format_file_size(needed_bytes)}). Aborted."
                            )

                with open(dest, file_mode) as fh:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        fh.write(chunk)
                        downloaded_bytes += len(chunk)

                        if downloaded_bytes > max_bytes:
                            dest.unlink(missing_ok=True)
                            raise ValueError(f"Download exceeded maximum allowed limit ({utils.format_file_size(max_bytes)}). Aborted.")

                        if progress_callback:
                            progress_callback(downloaded_bytes, total_size)

            # Check if download finished completely
            if total_size > 0 and downloaded_bytes < total_size:
                raise http.client.IncompleteRead(bytes(), expected=total_size - downloaded_bytes)

            # Clean completion
            break

        except (http.client.IncompleteRead, urllib.error.URLError, socket.timeout, ConnectionResetError, OSError) as exc:
            retry_count += 1
            if retry_count > max_retries:
                logger.error("Download failed after %d retries for %s: %s", max_retries, target_url, exc)
                raise IOError(f"Connection dropped and failed after {max_retries} resume attempts: {exc}") from exc

            logger.warning(
                "Stream interrupted at %s / %s (%s). Resuming (%d/%d) in %ds...",
                utils.format_file_size(downloaded_bytes),
                utils.format_file_size(total_size),
                exc,
                retry_count,
                max_retries,
                retry_count * 2,
            )
            time.sleep(retry_count * 2)

    if progress_callback and total_size > 0:
        progress_callback(total_size, total_size)

    return dest, downloaded_bytes, mime_type


# ---------------- PENDING CONFIRMATION TASKS CACHE ---------------- #

def cleanup_expired_tasks() -> None:
    """Purge tasks older than PENDING_TASK_TTL."""
    now = time.time()
    expired = [k for k, v in _PENDING_TASKS.items() if now - v.get("created_at", 0) > PENDING_TASK_TTL]
    for k in expired:
        _PENDING_TASKS.pop(k, None)


def store_pending_task(task_id: str, data: Dict[str, Any]) -> None:
    """Store pending link task awaiting user confirmation."""
    cleanup_expired_tasks()
    data["created_at"] = time.time()
    _PENDING_TASKS[task_id] = data


def get_pending_task(task_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve pending link task."""
    cleanup_expired_tasks()
    return _PENDING_TASKS.get(task_id)


def pop_pending_task(task_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve and remove pending link task."""
    cleanup_expired_tasks()
    return _PENDING_TASKS.pop(task_id, None)
