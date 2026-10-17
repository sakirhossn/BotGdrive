"""Unit tests for URL Downloader & SSRF Protection."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock

import config
import url_service
import utils


class TestUrlDownloader(unittest.TestCase):

    def test_ssrf_blocking_private_ips(self) -> None:
        """Verify SSRF blocks loopback, private RFC 1918, and cloud metadata addresses."""
        blocked_urls = [
            "http://127.0.0.1/secret",
            "http://localhost:8000/api",
            "http://10.0.0.1/admin",
            "http://192.168.1.1/router",
            "http://172.16.0.1/internal",
            "http://169.254.169.254/latest/meta-data/",
            "http://metadata.google.internal/computeMetadata/v1/",
            "ftp://example.com/file.txt",
            "file:///etc/passwd",
        ]
        for url in blocked_urls:
            with self.subTest(url=url):
                with self.assertRaises(url_service.SSRFSecurityError):
                    url_service.validate_url_security(url)

    def test_dropbox_url_normalization(self) -> None:
        """Verify Dropbox ?dl=0 is rewritten to ?dl=1."""
        raw = "https://www.dropbox.com/s/sample123/video.mp4?dl=0"
        transformed, provider = url_service.normalize_provider_url(raw)
        self.assertIn("dl=1", transformed)
        self.assertEqual(provider, "Dropbox")

    def test_gdrive_url_normalization(self) -> None:
        """Verify Google Drive share links are rewritten to export=download."""
        raw = "https://drive.google.com/file/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/view"
        transformed, provider = url_service.normalize_provider_url(raw)
        self.assertIn("export=download", transformed)
        self.assertIn("1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms", transformed)
        self.assertEqual(provider, "Google Drive (Public Share)")

    def test_filename_extraction_from_disposition(self) -> None:
        """Verify extracting filenames from Content-Disposition headers."""
        headers = MagicMock()
        headers.get.side_effect = lambda k, default="": {
            "Content-Disposition": 'attachment; filename="Quarterly_Report_2026.pdf"',
            "Content-Type": "application/pdf",
        }.get(k, default)

        name = url_service.extract_filename_from_headers(headers, "https://example.com/download?id=99")
        self.assertEqual(name, "Quarterly_Report_2026.pdf")

    def test_filename_extraction_rfc5987(self) -> None:
        """Verify extracting UTF-8 encoded filenames from RFC 5987 filename*."""
        headers = MagicMock()
        headers.get.side_effect = lambda k, default="": {
            "Content-Disposition": "attachment; filename*=UTF-8''Vacation%20Photos%202026.zip",
            "Content-Type": "application/zip",
        }.get(k, default)

        name = url_service.extract_filename_from_headers(headers, "https://example.com/get")
        self.assertEqual(name, "Vacation Photos 2026.zip")

    def test_filename_fallback_to_url_path(self) -> None:
        """Verify falling back to URL path if Content-Disposition is absent."""
        headers = MagicMock()
        headers.get.return_value = ""

        name = url_service.extract_filename_from_headers(headers, "https://cdn.example.org/media/song_track.flac")
        self.assertEqual(name, "song_track.flac")

    def test_pending_task_cache(self) -> None:
        """Verify storing, retrieving, and popping pending link confirmation tasks."""
        task_id = "test_abc123"
        task_data = {
            "url": "https://example.com/test.zip",
            "filename": "test.zip",
            "size_bytes": 1024,
            "target_id": "root_folder",
        }
        url_service.store_pending_task(task_id, task_data)
        retrieved = url_service.get_pending_task(task_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["filename"], "test.zip")

        popped = url_service.pop_pending_task(task_id)
        self.assertEqual(popped["filename"], "test.zip")
        self.assertIsNone(url_service.get_pending_task(task_id))

    def test_disk_space_guard(self) -> None:
        """Verify disk space check flags impossibly large requests."""
        # Realistic size should pass
        is_ok, free, needed = utils.check_disk_space(1024, config.DOWNLOADS_DIR)
        self.assertTrue(is_ok)
        self.assertGreater(free, 0)

        # 1 Petabyte request must fail
        is_ok_huge, free_huge, needed_huge = utils.check_disk_space(10**15, config.DOWNLOADS_DIR)
        self.assertFalse(is_ok_huge)

    def test_format_helpers(self) -> None:
        """Verify shrunken formatters match expectations."""
        self.assertEqual(utils.format_file_size(0), "0 B")
        self.assertEqual(utils.format_file_size(1024), "1.0 KB")
        self.assertEqual(utils.format_file_size(1048576), "1.0 MB")
        self.assertEqual(utils.format_time(45), "45s")
        self.assertEqual(utils.format_time(90), "1m 30s")
        self.assertEqual(utils.format_time(3665), "1h 1m")

    def test_stream_url_to_file_resume(self) -> None:
        """Verify stream_url_to_file sends Range header and resumes when connection drops."""
        import tempfile
        from unittest.mock import patch

        total_bytes = 1000
        part1 = b"A" * 400
        part2 = b"B" * 600

        # Mock first response: sends 400 bytes then EOF while total_size is 1000
        resp1 = MagicMock()
        resp1.geturl.return_value = "https://example.com/data.bin"
        resp1.headers = {"Content-Length": str(total_bytes), "Content-Type": "application/octet-stream"}
        resp1.read.side_effect = [part1, b""]  # Premature EOF triggers IncompleteRead

        # Mock second response: sends remaining 600 bytes
        resp2 = MagicMock()
        resp2.geturl.return_value = "https://example.com/data.bin"
        resp2.headers = {"Content-Length": str(len(part2)), "Content-Type": "application/octet-stream"}
        resp2.read.side_effect = [part2, b""]

        mock_opener = MagicMock()
        # Return resp1 as context manager, then resp2
        mock_opener.open.side_effect = [resp1, resp2]
        resp1.__enter__.return_value = resp1
        resp2.__enter__.return_value = resp2

        with tempfile.TemporaryDirectory() as tmpdir:
            dest_file = Path(tmpdir) / "output.bin"
            with patch("url_service.build_safe_opener", return_value=mock_opener), \
                 patch("url_service.validate_url_security", return_value="https://example.com/data.bin"), \
                 patch("time.sleep", return_value=None):
                
                dest, downloaded, mime = url_service.stream_url_to_file(
                    "https://example.com/data.bin",
                    dest_file,
                    timeout=5.0,
                    max_retries=2,
                )

                self.assertEqual(downloaded, total_bytes)
                self.assertEqual(dest.read_bytes(), part1 + part2)
                # Verify second request included Range header
                second_call_req = mock_opener.open.call_args_list[1][0][0]
                self.assertEqual(second_call_req.headers.get("Range"), "bytes=400-")

    def test_progress_tracker_windowed_speed(self) -> None:
        """Verify ProgressTracker calculates responsive speed and formats card."""
        tracker = utils.ProgressTracker(
            action_name="📥 Downloading",
            filename="test_video.mp4",
            total_bytes=100 * 1024 * 1024,
            update_interval=0.1,
        )
        card1 = tracker.format_status(10 * 1024 * 1024)
        self.assertIn("test_video.mp4", card1)
        self.assertIn("10.0%", card1)
        self.assertIn("Speed:", card1)


if __name__ == "__main__":
    unittest.main()
