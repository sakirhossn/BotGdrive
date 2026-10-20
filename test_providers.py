"""Unit tests for Modular URL Providers & ProviderRegistry (Stage 2)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from providers.base import BaseProvider, DownloadResult, ProviderMetadata
from providers.direct_http import (
    DirectHttpProvider,
    SSRFSecurityError,
    extract_filename_from_headers,
    is_ip_allowed,
    validate_url_security,
)
from providers.registry import ProviderRegistry


class TestDirectHttpProvider(unittest.TestCase):

    def setUp(self) -> None:
        self.provider = DirectHttpProvider()

    def test_can_handle(self) -> None:
        """Verify DirectHttpProvider accepts valid http/https URLs and rejects invalid ones."""
        self.assertTrue(self.provider.can_handle("http://example.com/file.mp4"))
        self.assertTrue(self.provider.can_handle("https://cdn.site.org/docs/manual.pdf"))
        self.assertFalse(self.provider.can_handle("ftp://example.com/file.txt"))
        self.assertFalse(self.provider.can_handle("file:///etc/passwd"))
        self.assertFalse(self.provider.can_handle("invalid-url"))

    def test_ssrf_validation(self) -> None:
        """Verify SSRF blocks private and loopback networks."""
        blocked = [
            "http://127.0.0.1/test",
            "http://localhost:8080/data",
            "http://10.0.0.1/admin",
            "http://192.168.1.1/secret",
            "http://172.16.0.1/internal",
            "http://169.254.169.254/latest/meta-data/",
            "http://metadata.google.internal/computeMetadata/v1/",
        ]
        for url in blocked:
            with self.subTest(url=url):
                with self.assertRaises(SSRFSecurityError):
                    validate_url_security(url)

    def test_probe_direct_file(self) -> None:
        """Verify probe on direct file returns proper metadata."""
        mock_resp = MagicMock()
        mock_resp.geturl.return_value = "https://example.com/archive.zip"
        mock_headers = MagicMock()
        mock_headers.get.side_effect = lambda k, d="": {
            "Content-Type": "application/zip",
            "Content-Length": "10485760",
            "Content-Disposition": 'attachment; filename="archive.zip"',
        }.get(k, d)
        mock_resp.headers = mock_headers

        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp

        with patch("providers.direct_http.build_safe_opener", return_value=mock_opener), \
             patch("providers.direct_http.validate_url_security", return_value="https://example.com/archive.zip"):
            
            meta = self.provider.probe("https://example.com/archive.zip")
            self.assertEqual(meta.filename, "archive.zip")
            self.assertEqual(meta.size_bytes, 10485760)
            self.assertEqual(meta.mime_type, "application/zip")
            self.assertTrue(meta.is_direct)
            self.assertFalse(meta.is_html)
            self.assertEqual(meta.provider_name, "Direct HTTP")

    def test_probe_html_webpage(self) -> None:
        """Verify probe flags HTML webpages."""
        mock_resp = MagicMock()
        mock_resp.geturl.return_value = "https://example.com/watch?v=123"
        mock_headers = MagicMock()
        mock_headers.get.side_effect = lambda k, d="": {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Length": "45000",
        }.get(k, d)
        mock_resp.headers = mock_headers

        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp

        with patch("providers.direct_http.build_safe_opener", return_value=mock_opener), \
             patch("providers.direct_http.validate_url_security", return_value="https://example.com/watch?v=123"):
            
            meta = self.provider.probe("https://example.com/watch?v=123")
            self.assertTrue(meta.is_html)
            self.assertFalse(meta.is_direct)

    def test_download_direct_stream(self) -> None:
        """Verify streaming download writes bytes directly to destination file."""
        data = b"Hello, this is test file stream data."
        mock_resp = MagicMock()
        mock_resp.geturl.return_value = "https://example.com/document.txt"
        mock_resp.headers = {"Content-Length": str(len(data)), "Content-Type": "text/plain"}
        mock_resp.read.side_effect = [data, b""]

        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        mock_resp.__enter__.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "document.txt"
            with patch("providers.direct_http.build_safe_opener", return_value=mock_opener), \
                 patch("providers.direct_http.validate_url_security", return_value="https://example.com/document.txt"):
                
                result = self.provider.download(
                    "https://example.com/document.txt",
                    dest,
                )

                self.assertEqual(result.downloaded_bytes, len(data))
                self.assertEqual(result.mime_type, "text/plain")
                self.assertEqual(dest.read_bytes(), data)

    def test_download_range_resumption(self) -> None:
        """Verify Range resumption reconnects and appends when connection drops."""
        part1 = b"ChunkOne"
        part2 = b"ChunkTwo"
        total_len = len(part1) + len(part2)

        resp1 = MagicMock()
        resp1.geturl.return_value = "https://example.com/media.bin"
        resp1.headers = {"Content-Length": str(total_len), "Content-Type": "application/octet-stream"}
        resp1.read.side_effect = [part1, b""]  # Premature EOF

        resp2 = MagicMock()
        resp2.geturl.return_value = "https://example.com/media.bin"
        resp2.headers = {"Content-Length": str(len(part2)), "Content-Type": "application/octet-stream"}
        resp2.read.side_effect = [part2, b""]

        mock_opener = MagicMock()
        mock_opener.open.side_effect = [resp1, resp2]
        resp1.__enter__.return_value = resp1
        resp2.__enter__.return_value = resp2

        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "media.bin"
            with patch("providers.direct_http.build_safe_opener", return_value=mock_opener), \
                 patch("providers.direct_http.validate_url_security", return_value="https://example.com/media.bin"), \
                 patch("time.sleep", return_value=None):
                
                result = self.provider.download(
                    "https://example.com/media.bin",
                    dest,
                    max_retries=2,
                )

                self.assertEqual(result.downloaded_bytes, total_len)
                self.assertEqual(dest.read_bytes(), part1 + part2)


class TestProviderRegistry(unittest.TestCase):

    def test_registry_fallback(self) -> None:
        """Verify registry falls back to DirectHttpProvider for generic URLs."""
        registry = ProviderRegistry()
        provider = registry.get_provider("https://example.com/song.flac")
        self.assertEqual(provider.name, "Direct HTTP")

    def test_registry_priority_dispatch(self) -> None:
        """Verify registered custom provider takes precedence when it matches."""
        class MockCustomProvider(BaseProvider):
            name = "MockCustom"

            def can_handle(self, url: str) -> bool:
                return "custom.org" in url

        registry = ProviderRegistry()
        custom = MockCustomProvider()
        registry.register(custom)

        matched = registry.get_provider("https://custom.org/item/42")
        self.assertEqual(matched.name, "MockCustom")

        fallback = registry.get_provider("https://other.com/file.zip")
        self.assertEqual(fallback.name, "Direct HTTP")


class TestCloudShareProviders(unittest.TestCase):

    def test_dropbox_provider(self) -> None:
        """Verify Dropbox provider handles URLs and normalizes dl=1."""
        from providers.cloud_shares import DropboxProvider

        provider = DropboxProvider()
        url = "https://www.dropbox.com/s/abcdef12345/vacation.mp4?dl=0"
        self.assertTrue(provider.can_handle(url))
        self.assertFalse(provider.can_handle("https://example.com/file.mp4"))

        normalized = provider.normalize_url(url)
        self.assertIn("dl=1", normalized)

    def test_gdrive_public_provider(self) -> None:
        """Verify Google Drive public provider extracts file ID and builds direct export URL."""
        from providers.cloud_shares import GDrivePublicProvider

        provider = GDrivePublicProvider()
        url1 = "https://drive.google.com/file/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/view"
        url2 = "https://docs.google.com/open?id=abc123xyz"
        self.assertTrue(provider.can_handle(url1))
        self.assertTrue(provider.can_handle(url2))
        self.assertFalse(provider.can_handle("https://youtube.com/watch?v=123"))

        self.assertEqual(provider.extract_file_id(url1), "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms")
        self.assertEqual(provider.extract_file_id(url2), "abc123xyz")

        direct = provider.get_direct_url(url1)
        self.assertIn("uc?export=download", direct)
        self.assertIn("1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms", direct)

    def test_github_provider(self) -> None:
        """Verify GitHub provider recognizes release assets and raw file links."""
        from providers.cloud_shares import GitHubProvider

        provider = GitHubProvider()
        release_url = "https://github.com/user/repo/releases/download/v1.0.0/setup.zip"
        raw_url = "https://raw.githubusercontent.com/user/repo/main/config.json"
        repo_url = "https://github.com/user/repo"

        self.assertTrue(provider.can_handle(release_url))
        self.assertTrue(provider.can_handle(raw_url))
        self.assertFalse(provider.can_handle(repo_url))

    def test_default_registry_dispatch(self) -> None:
        """Verify global default_registry dispatches to the correct cloud share providers."""
        from providers.registry import default_registry

        self.assertEqual(
            default_registry.get_provider("https://www.dropbox.com/s/xyz/video.mp4").name,
            "Dropbox",
        )
        self.assertEqual(
            default_registry.get_provider("https://drive.google.com/file/d/123/view").name,
            "Google Drive (Public Share)",
        )
        self.assertEqual(
            default_registry.get_provider("https://github.com/user/repo/releases/download/v1/app.exe").name,
            "GitHub Asset",
        )
        self.assertEqual(
            default_registry.get_provider("https://example.com/file.iso").name,
            "Direct HTTP",
        )
        self.assertEqual(
            default_registry.get_provider("https://www.youtube.com/watch?v=dQw4w9WgXcQ").name,
            "Media Extractor",
        )
        self.assertEqual(
            default_registry.get_provider("https://facebook.com/reel/123456789/").name,
            "Media Extractor",
        )


class TestMediaExtractorProvider(unittest.TestCase):

    def setUp(self) -> None:
        from providers.media_extractor import MediaExtractorProvider
        self.provider = MediaExtractorProvider()

    def test_can_handle_domains(self) -> None:
        """Verify MediaExtractor handles YouTube, Facebook Reels, and media hosts."""
        self.assertTrue(self.provider.can_handle("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        self.assertTrue(self.provider.can_handle("https://youtu.be/dQw4w9WgXcQ"))
        self.assertTrue(self.provider.can_handle("https://youtube.com/shorts/abcdef123"))
        self.assertTrue(self.provider.can_handle("https://m.youtube.com/watch?v=test"))
        self.assertTrue(self.provider.can_handle("https://www.facebook.com/reel/123456789/"))
        self.assertTrue(self.provider.can_handle("https://fb.watch/xyz123/"))
        self.assertTrue(self.provider.can_handle("https://vimeo.com/123456"))
        self.assertTrue(self.provider.can_handle("https://soundcloud.com/artist/track"))

        self.assertFalse(self.provider.can_handle("https://example.com/document.pdf"))
        self.assertFalse(self.provider.can_handle("https://drive.google.com/file/d/123/view"))

    def test_probe_media(self) -> None:
        """Verify media probe extracts title, ext, size, and duration."""
        mock_info = {
            "title": "Amazing Nature 4K",
            "id": "vid123",
            "ext": "mp4",
            "filesize": 52428800,  # 50 MB
            "duration": 180,
            "thumbnail": "https://i.ytimg.com/vi/vid123/hqdefault.jpg",
            "uploader": "NatureChannel",
            "webpage_url": "https://www.youtube.com/watch?v=vid123",
        }

        mock_ydl_instance = MagicMock()
        mock_ydl_instance.extract_info.return_value = mock_info
        mock_ydl_class = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        with patch("yt_dlp.YoutubeDL", mock_ydl_class):
            meta = self.provider.probe("https://www.youtube.com/watch?v=vid123")
            self.assertEqual(meta.filename, "Amazing Nature 4K.mp4")
            self.assertEqual(meta.size_bytes, 52428800)
            self.assertEqual(meta.mime_type, "video/mp4")
            self.assertTrue(meta.is_direct)
            self.assertFalse(meta.is_html)
            self.assertIn("YouTube", meta.provider_name)
            self.assertEqual(meta.extra["duration"], 180)

    def test_probe_drm_protection_error(self) -> None:
        """Verify DRM-protected videos raise PermissionError with clear message."""
        import yt_dlp

        mock_ydl_instance = MagicMock()
        mock_ydl_instance.extract_info.side_effect = yt_dlp.utils.DownloadError("This video is DRM protected and cannot be downloaded.")
        mock_ydl_class = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        with patch("yt_dlp.YoutubeDL", mock_ydl_class):
            with self.assertRaises(PermissionError) as ctx:
                self.provider.probe("https://www.youtube.com/watch?v=drm_video")
            self.assertIn("DRM-protected", str(ctx.exception))

    def test_probe_private_video_error(self) -> None:
        """Verify private or login-required videos raise PermissionError with clear message."""
        import yt_dlp

        mock_ydl_instance = MagicMock()
        mock_ydl_instance.extract_info.side_effect = yt_dlp.utils.DownloadError("Private video. Sign in if you've been granted access.")
        mock_ydl_class = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        with patch("yt_dlp.YoutubeDL", mock_ydl_class):
            with self.assertRaises(PermissionError) as ctx:
                self.provider.probe("https://www.youtube.com/watch?v=private_video")
            self.assertIn("private login", str(ctx.exception))

    def test_download_media_mock(self) -> None:
        """Verify media download hooks progress and returns DownloadResult."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dest_file = Path(tmpdir) / "test_out.mp4"
            actual_file = Path(tmpdir) / "test_out.mp4"
            actual_file.write_bytes(b"Simulated MP4 video bytes" * 100)

            mock_info = {
                "title": "Test Video",
                "requested_downloads": [{"filepath": str(actual_file)}],
            }

            mock_ydl_instance = MagicMock()
            mock_ydl_instance.extract_info.return_value = mock_info
            mock_ydl_class = MagicMock()
            mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

            with patch("yt_dlp.YoutubeDL", mock_ydl_class):
                result = self.provider.download(
                    "https://www.youtube.com/watch?v=test",
                    dest_file,
                )
                self.assertEqual(result.dest_path, actual_file)
                self.assertEqual(result.downloaded_bytes, actual_file.stat().st_size)
                self.assertIn("YouTube", result.provider_name)


    def test_additional_cloud_providers(self) -> None:
        """Verify Pixeldrain, Mediafire, and Catbox providers."""
        from providers.cloud_shares import CatboxProvider, MediafireProvider, PixeldrainProvider

        # Pixeldrain
        pd = PixeldrainProvider()
        self.assertTrue(pd.can_handle("https://pixeldrain.com/u/abc12345"))
        self.assertEqual(pd.normalize_url("https://pixeldrain.com/u/abc12345"), "https://pixeldrain.com/api/file/abc12345")
        self.assertFalse(pd.can_handle("https://example.com/file"))

        # Mediafire
        mf = MediafireProvider()
        self.assertTrue(mf.can_handle("https://www.mediafire.com/file/xyz123/doc.pdf/file"))
        self.assertFalse(mf.can_handle("https://dropbox.com/s/123"))

        # Catbox
        cb = CatboxProvider()
        self.assertTrue(cb.can_handle("https://files.catbox.moe/file.mp4"))
        self.assertTrue(cb.can_handle("https://litterbox.catbox.moe/temp.zip"))
        self.assertFalse(cb.can_handle("https://youtube.com"))

    def test_expanded_media_domains(self) -> None:
        """Verify broadened media domain recognition (Twitter/X, Reddit, Threads, Pinterest, etc.)."""
        test_urls = [
            "https://twitter.com/user/status/123456789",
            "https://x.com/user/status/987654321",
            "https://www.reddit.com/r/videos/comments/xyz/clip",
            "https://threads.net/@user/post/abc123",
            "https://www.pinterest.com/pin/123456789/",
            "https://pin.it/abcXYZ",
            "https://www.twitch.tv/videos/123456",
            "https://streamable.com/abc12",
        ]
        for url in test_urls:
            with self.subTest(url=url):
                self.assertTrue(self.provider.can_handle(url))

    def test_registry_html_fallback_to_media(self) -> None:
        """Verify ProviderRegistry falls back to MediaExtractor when Direct HTTP detects HTML."""
        from providers.registry import ProviderRegistry
        reg = ProviderRegistry()
        reg.register(self.provider)  # MediaExtractorProvider

        mock_direct = MagicMock()
        mock_direct.name = "Direct HTTP"
        mock_direct.can_handle.return_value = True
        mock_direct.probe.return_value = ProviderMetadata(
            url="https://unknown-video-host.com/watch?v=123",
            final_url="https://unknown-video-host.com/watch?v=123",
            provider_name="Direct HTTP",
            filename="watch.html",
            is_html=True,
            is_direct=False,
        )
        reg.register(mock_direct)

        with patch.object(self.provider, "probe") as mock_media_probe:
            mock_media_probe.return_value = ProviderMetadata(
                url="https://unknown-video-host.com/watch?v=123",
                final_url="https://unknown-video-host.com/watch?v=123",
                provider_name="Media Extractor",
                filename="cool_video.mp4",
                is_html=False,
                is_direct=True,
                size_bytes=1048576,
            )
            meta = reg.probe("https://unknown-video-host.com/watch?v=123")
            self.assertFalse(meta.is_html)
            self.assertEqual(meta.filename, "cool_video.mp4")


if __name__ == "__main__":
    unittest.main()


