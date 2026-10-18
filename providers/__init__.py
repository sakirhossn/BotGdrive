"""Providers package for Universal Link Downloader."""

from providers.base import BaseProvider, DownloadResult, ProviderMetadata
from providers.cloud_shares import DropboxProvider, GDrivePublicProvider, GitHubProvider
from providers.direct_http import (
    DirectHttpProvider,
    SSRFSecurityError,
    extract_filename_from_headers,
    is_ip_allowed,
    validate_url_security,
)
from providers.media_extractor import MediaExtractorProvider
from providers.registry import ProviderRegistry, default_registry, get_default_registry

__all__ = [
    "BaseProvider",
    "DownloadResult",
    "ProviderMetadata",
    "DirectHttpProvider",
    "DropboxProvider",
    "GDrivePublicProvider",
    "GitHubProvider",
    "MediaExtractorProvider",
    "SSRFSecurityError",
    "is_ip_allowed",
    "validate_url_security",
    "extract_filename_from_headers",
    "ProviderRegistry",
    "default_registry",
    "get_default_registry",
]
