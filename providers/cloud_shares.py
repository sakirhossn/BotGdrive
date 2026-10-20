"""Cloud File Sharing Providers: Dropbox, Google Drive Public Shares, and GitHub Assets."""

from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Callable, Optional, Tuple
import urllib.parse

from providers.base import BaseProvider, DownloadResult, ProviderMetadata
from providers.direct_http import DirectHttpProvider

logger = logging.getLogger(__name__)


class DropboxProvider(BaseProvider):
    """Provider for public Dropbox shared links."""

    name: str = "Dropbox"

    def __init__(self, http_provider: Optional[DirectHttpProvider] = None) -> None:
        self.http_provider = http_provider or DirectHttpProvider()

    def can_handle(self, url: str) -> bool:
        """Check if URL points to dropbox.com."""
        try:
            parsed = urllib.parse.urlsplit(url)
            return "dropbox.com" in (parsed.hostname or "").lower()
        except Exception:
            return False

    def normalize_url(self, raw_url: str) -> str:
        """Rewrite Dropbox link query parameters so it points directly to binary content (dl=1)."""
        parsed = urllib.parse.urlsplit(raw_url)
        query = urllib.parse.parse_qs(parsed.query)
        query["dl"] = ["1"]
        new_query = urllib.parse.urlencode(query, doseq=True)
        return urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment)
        )

    def probe(self, url: str, timeout: float = 12.0) -> ProviderMetadata:
        """Probe Dropbox direct download link."""
        direct_url = self.normalize_url(url)
        meta = self.http_provider.probe(direct_url, timeout=timeout)
        meta.url = url
        meta.provider_name = self.name
        return meta

    def download(
        self,
        url: str,
        dest_path: Path | str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        max_bytes: int = 2000 * 1024 * 1024,
        timeout: float = 30.0,
        max_retries: int = 5,
    ) -> DownloadResult:
        """Download public Dropbox file."""
        direct_url = self.normalize_url(url)
        res = self.http_provider.download(
            url=direct_url,
            dest_path=dest_path,
            progress_callback=progress_callback,
            status_callback=status_callback,
            max_bytes=max_bytes,
            timeout=timeout,
            max_retries=max_retries,
        )
        res.provider_name = self.name
        return res


class GDrivePublicProvider(BaseProvider):
    """Provider for public Google Drive shared links."""

    name: str = "Google Drive (Public Share)"

    def __init__(self, http_provider: Optional[DirectHttpProvider] = None) -> None:
        self.http_provider = http_provider or DirectHttpProvider()

    def can_handle(self, url: str) -> bool:
        """Check if URL points to Google Drive share."""
        try:
            parsed = urllib.parse.urlsplit(url)
            host = (parsed.hostname or "").lower()
            return host in ("drive.google.com", "docs.google.com")
        except Exception:
            return False

    def extract_file_id(self, url: str) -> Optional[str]:
        """Extract Google Drive file ID from URL."""
        parsed = urllib.parse.urlsplit(url)
        m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", parsed.path)
        if m:
            return m.group(1)

        m = re.search(r"/d/([a-zA-Z0-9_-]+)", parsed.path)
        if m:
            return m.group(1)

        qs = urllib.parse.parse_qs(parsed.query)
        if "id" in qs:
            return qs["id"][0]

        return None

    def get_direct_url(self, url: str) -> str:
        """Convert Google Drive share link to direct download stream."""
        file_id = self.extract_file_id(url)
        if file_id:
            # &confirm=t bypasses standard virus scan warning on files up to 100MB
            return f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t"
        return url

    def probe(self, url: str, timeout: float = 12.0) -> ProviderMetadata:
        """Probe public Google Drive share."""
        direct_url = self.get_direct_url(url)
        meta = self.http_provider.probe(direct_url, timeout=timeout)
        meta.url = url
        meta.provider_name = self.name
        return meta

    def download(
        self,
        url: str,
        dest_path: Path | str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        max_bytes: int = 2000 * 1024 * 1024,
        timeout: float = 30.0,
        max_retries: int = 5,
    ) -> DownloadResult:
        """Download public Google Drive file."""
        direct_url = self.get_direct_url(url)
        res = self.http_provider.download(
            url=direct_url,
            dest_path=dest_path,
            progress_callback=progress_callback,
            status_callback=status_callback,
            max_bytes=max_bytes,
            timeout=timeout,
            max_retries=max_retries,
        )
        res.provider_name = self.name
        return res


class GitHubProvider(BaseProvider):
    """Provider for GitHub Release assets and Raw files."""

    name: str = "GitHub Asset"

    def __init__(self, http_provider: Optional[DirectHttpProvider] = None) -> None:
        self.http_provider = http_provider or DirectHttpProvider()

    def can_handle(self, url: str) -> bool:
        """Check if URL points to GitHub release binary or raw file."""
        try:
            parsed = urllib.parse.urlsplit(url)
            host = (parsed.hostname or "").lower()
            if host == "raw.githubusercontent.com":
                return True
            if "github.com" in host and "/releases/download/" in parsed.path:
                return True
            return False
        except Exception:
            return False

    def probe(self, url: str, timeout: float = 12.0) -> ProviderMetadata:
        """Probe GitHub asset."""
        meta = self.http_provider.probe(url, timeout=timeout)
        meta.provider_name = self.name
        return meta

    def download(
        self,
        url: str,
        dest_path: Path | str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        max_bytes: int = 2000 * 1024 * 1024,
        timeout: float = 30.0,
        max_retries: int = 5,
    ) -> DownloadResult:
        """Download GitHub asset."""
        res = self.http_provider.download(
            url=url,
            dest_path=dest_path,
            progress_callback=progress_callback,
            status_callback=status_callback,
            max_bytes=max_bytes,
            timeout=timeout,
            max_retries=max_retries,
        )
        res.provider_name = self.name
        return res


class PixeldrainProvider(BaseProvider):
    """Provider for Pixeldrain public file downloads."""

    name: str = "Pixeldrain"

    def __init__(self, http_provider: Optional[DirectHttpProvider] = None) -> None:
        self.http_provider = http_provider or DirectHttpProvider()

    def can_handle(self, url: str) -> bool:
        try:
            parsed = urllib.parse.urlsplit(url)
            return "pixeldrain.com" in (parsed.hostname or "").lower()
        except Exception:
            return False

    def normalize_url(self, raw_url: str) -> str:
        """Convert pixeldrain.com/u/{id} or /d/{id} to direct download API link."""
        parsed = urllib.parse.urlsplit(raw_url)
        m = re.search(r"/(?:u|d)/([a-zA-Z0-9_-]+)", parsed.path)
        if m:
            file_id = m.group(1)
            return f"https://pixeldrain.com/api/file/{file_id}"
        return raw_url

    def probe(self, url: str, timeout: float = 12.0) -> ProviderMetadata:
        direct_url = self.normalize_url(url)
        meta = self.http_provider.probe(direct_url, timeout=timeout)
        meta.url = url
        meta.provider_name = self.name
        return meta

    def download(
        self,
        url: str,
        dest_path: Path | str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        max_bytes: int = 2000 * 1024 * 1024,
        timeout: float = 30.0,
        max_retries: int = 5,
    ) -> DownloadResult:
        direct_url = self.normalize_url(url)
        res = self.http_provider.download(
            url=direct_url,
            dest_path=dest_path,
            progress_callback=progress_callback,
            status_callback=status_callback,
            max_bytes=max_bytes,
            timeout=timeout,
            max_retries=max_retries,
        )
        res.provider_name = self.name
        return res


class MediafireProvider(BaseProvider):
    """Provider for Mediafire public downloads."""

    name: str = "Mediafire"

    def __init__(self, http_provider: Optional[DirectHttpProvider] = None) -> None:
        self.http_provider = http_provider or DirectHttpProvider()

    def can_handle(self, url: str) -> bool:
        try:
            parsed = urllib.parse.urlsplit(url)
            return "mediafire.com" in (parsed.hostname or "").lower()
        except Exception:
            return False

    def resolve_direct_url(self, raw_url: str, timeout: float = 12.0) -> str:
        """Scrape direct download link from Mediafire landing page."""
        try:
            req = urllib.request.Request(
                raw_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                html_bytes = resp.read(128 * 1024)
                html_text = html_bytes.decode("utf-8", errors="ignore")
                m = re.search(r'href="(https?://download\d*\.mediafire\.com/[^"]+)"', html_text)
                if m:
                    return m.group(1)
                m2 = re.search(r'aria-label="Download file"\s+href="([^"]+)"', html_text)
                if m2:
                    return m2.group(1)
        except Exception as exc:
            logger.warning("Could not extract Mediafire direct link: %s", exc)
        return raw_url

    def probe(self, url: str, timeout: float = 12.0) -> ProviderMetadata:
        direct_url = self.resolve_direct_url(url, timeout=timeout)
        meta = self.http_provider.probe(direct_url, timeout=timeout)
        meta.url = url
        meta.provider_name = self.name
        return meta

    def download(
        self,
        url: str,
        dest_path: Path | str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        max_bytes: int = 2000 * 1024 * 1024,
        timeout: float = 30.0,
        max_retries: int = 5,
    ) -> DownloadResult:
        direct_url = self.resolve_direct_url(url, timeout=timeout)
        res = self.http_provider.download(
            url=direct_url,
            dest_path=dest_path,
            progress_callback=progress_callback,
            status_callback=status_callback,
            max_bytes=max_bytes,
            timeout=timeout,
            max_retries=max_retries,
        )
        res.provider_name = self.name
        return res


class CatboxProvider(BaseProvider):
    """Provider for Catbox and Litterbox direct uploads."""

    name: str = "Catbox"

    def __init__(self, http_provider: Optional[DirectHttpProvider] = None) -> None:
        self.http_provider = http_provider or DirectHttpProvider()

    def can_handle(self, url: str) -> bool:
        try:
            parsed = urllib.parse.urlsplit(url)
            return "catbox.moe" in (parsed.hostname or "").lower()
        except Exception:
            return False

    def probe(self, url: str, timeout: float = 12.0) -> ProviderMetadata:
        meta = self.http_provider.probe(url, timeout=timeout)
        meta.provider_name = self.name
        return meta

    def download(
        self,
        url: str,
        dest_path: Path | str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        max_bytes: int = 2000 * 1024 * 1024,
        timeout: float = 30.0,
        max_retries: int = 5,
    ) -> DownloadResult:
        res = self.http_provider.download(
            url=url,
            dest_path=dest_path,
            progress_callback=progress_callback,
            status_callback=status_callback,
            max_bytes=max_bytes,
            timeout=timeout,
            max_retries=max_retries,
        )
        res.provider_name = self.name
        return res

