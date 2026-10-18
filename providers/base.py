"""Base classes and data structures for URL download providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple


@dataclass
class ProviderMetadata:
    """Metadata extracted during URL probe."""

    url: str
    final_url: str
    provider_name: str
    filename: str
    size_bytes: int = 0
    mime_type: str = "application/octet-stream"
    is_direct: bool = True
    is_html: bool = False
    host: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to standard dictionary for backward compatibility."""
        return {
            "url": self.url,
            "final_url": self.final_url,
            "provider": self.provider_name if self.provider_name != "Direct HTTP" else None,
            "provider_name": self.provider_name,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "mime_type": self.mime_type,
            "is_direct": self.is_direct,
            "is_html": self.is_html,
            "host": self.host,
            "extra": self.extra,
        }


@dataclass
class DownloadResult:
    """Result of a completed provider download."""

    dest_path: Path
    downloaded_bytes: int
    mime_type: str
    provider_name: str
    filename: str

    def to_tuple(self) -> Tuple[Path, int, str]:
        """Convert to (dest_path, downloaded_bytes, mime_type) tuple for legacy caller compatibility."""
        return (self.dest_path, self.downloaded_bytes, self.mime_type)


class BaseProvider:
    """Abstract base class for all link providers."""

    name: str = "Base"

    def can_handle(self, url: str) -> bool:
        """Check if this provider recognizes and can handle the given URL."""
        raise NotImplementedError

    def probe(self, url: str, timeout: float = 12.0) -> ProviderMetadata:
        """Probe URL to extract metadata (filename, size, MIME type, availability)."""
        raise NotImplementedError

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
        """Stream/download remote resource to local file path."""
        raise NotImplementedError
