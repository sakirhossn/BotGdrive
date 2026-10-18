"""Central Provider Registry for Universal Link Downloader."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List, Optional

from providers.base import BaseProvider, DownloadResult, ProviderMetadata
from providers.cloud_shares import DropboxProvider, GDrivePublicProvider, GitHubProvider
from providers.direct_http import DirectHttpProvider
from providers.media_extractor import MediaExtractorProvider

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Registry managing URL detection and dispatching to appropriate providers."""

    def __init__(self) -> None:
        self._providers: List[BaseProvider] = []
        self._fallback_provider: BaseProvider = DirectHttpProvider()

    def register(self, provider: BaseProvider, priority: int = -1) -> None:
        """Register a provider. If priority is given, insert at index, else append."""
        if priority >= 0 and priority < len(self._providers):
            self._providers.insert(priority, provider)
        else:
            self._providers.append(provider)
        logger.debug("Registered provider: %s", provider.name)

    def get_provider(self, url: str) -> BaseProvider:
        """Find the first provider that can handle the given URL, or return the default fallback."""
        for provider in self._providers:
            try:
                if provider.can_handle(url):
                    return provider
            except Exception as exc:
                logger.warning("Error checking can_handle for %s with url %s: %s", provider.name, url, exc)

        return self._fallback_provider

    def probe(self, url: str, timeout: float = 12.0) -> ProviderMetadata:
        """Probe a URL using the best matching provider."""
        provider = self.get_provider(url)
        logger.debug("Probing URL %s with provider %s", url, provider.name)
        return provider.probe(url, timeout=timeout)

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
        """Download a URL using the best matching provider."""
        provider = self.get_provider(url)
        logger.info("Executing download for '%s' using provider %s", url, provider.name)
        return provider.download(
            url=url,
            dest_path=dest_path,
            progress_callback=progress_callback,
            status_callback=status_callback,
            max_bytes=max_bytes,
            timeout=timeout,
            max_retries=max_retries,
        )


# Global default registry instance with prioritized providers
default_registry = ProviderRegistry()
default_registry.register(DropboxProvider())
default_registry.register(GDrivePublicProvider())
default_registry.register(GitHubProvider())
default_registry.register(MediaExtractorProvider())
default_registry.register(DirectHttpProvider())


def get_default_registry() -> ProviderRegistry:
    """Get the singleton default provider registry."""
    return default_registry

