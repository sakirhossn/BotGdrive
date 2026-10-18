"""URL Inspector, Stream Downloader, and Provider Integration Facade.

Maintains backward-compatible interfaces for handlers while delegating
to the modular providers package (providers/).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Dict, Optional, Tuple
import urllib.parse

import config
import providers
from providers.direct_http import (
    DEFAULT_MAX_DOWNLOAD_BYTES,
    SSRFSecurityError,
    SafeRedirectHandler,
    build_safe_opener,
    extract_filename_from_headers,
    is_ip_allowed,
    validate_url_security,
)
import utils

logger = logging.getLogger(__name__)

# Max file size: 2,000 MB (2 GB)
MAX_DOWNLOAD_BYTES: int = DEFAULT_MAX_DOWNLOAD_BYTES

# Pending confirmation tasks cache: {task_id: {"url": str, "name": str, "size": int, "mime": str, "target_id": str, "target_name": str, "created_at": float}}
_PENDING_TASKS: Dict[str, Dict[str, Any]] = {}
PENDING_TASK_TTL: float = 900.0  # 15 minutes


def normalize_provider_url(raw_url: str) -> Tuple[str, Optional[str]]:
    """Transform known public hosting links to direct downloadable streams.

    Returns:
        (transformed_url, provider_name)
    """
    parsed = urllib.parse.urlsplit(raw_url)
    hostname = (parsed.hostname or "").lower()

    # 1. Dropbox
    if "dropbox.com" in hostname:
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


def probe_url(url: str, timeout: float = 12.0) -> Dict[str, Any]:
    """Inspect a URL and extract metadata (filename, size, MIME type, direct status).

    Raises SSRFSecurityError if URL points to internal network.
    """
    target_url, provider_hint = normalize_provider_url(url)
    registry = providers.get_default_registry()
    meta = registry.probe(target_url, timeout=timeout)

    result = meta.to_dict()
    if provider_hint:
        result["provider"] = provider_hint
        result["provider_name"] = provider_hint
    return result


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
    registry = providers.get_default_registry()
    download_res = registry.download(
        url=target_url,
        dest_path=destination_path,
        progress_callback=progress_callback,
        status_callback=status_callback,
        max_bytes=max_bytes,
        timeout=timeout,
        max_retries=max_retries,
    )
    return download_res.to_tuple()


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
