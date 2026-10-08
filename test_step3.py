"""Unit tests for Step 3: Drive browsing, folder navigation, pagination, file details, send back, and search."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import config
import drive_service
import handlers
import utils


class TestBrowseAndNavigation(unittest.TestCase):
    def test_build_browse_keyboard_folders_first_and_pagination(self):
        folders = [{"id": f"fld_{i}", "name": f"Folder {i}"} for i in range(6)]
        files = [{"id": f"fil_{i}", "name": f"File {i}.pdf", "size": 1024 * 1024} for i in range(8)]

        # Page 1 (10 items total: 6 folders + 4 files)
        kb_p1 = handlers.build_browse_keyboard(folders, files, current_folder_id="root_id", parent_id=None, page=1)
        buttons_p1 = kb_p1.inline_keyboard

        # First 6 buttons should be folders
        for i in range(6):
            self.assertTrue(buttons_p1[i][0].callback_data.startswith("br:o:"))
            self.assertIn(f"Folder {i}", buttons_p1[i][0].text)

        # Next 4 buttons should be files
        for i in range(6, 10):
            self.assertTrue(buttons_p1[i][0].callback_data.startswith("fi:v:"))
            self.assertIn("File", buttons_p1[i][0].text)

        # Pagination controls row exists
        nav_row = [btn.callback_data for btn in buttons_p1[10]]
        self.assertIn("br:p:2", nav_row)  # Next button
        self.assertIn("1/2", [btn.text for btn in buttons_p1[10]])

    def test_build_browse_keyboard_back_and_root_in_subfolder(self):
        folders = []
        files = []
        # Current folder is not root
        kb = handlers.build_browse_keyboard(folders, files, current_folder_id="sub_123", parent_id="root_id", page=1)
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        self.assertIn("br:b:root_id", callbacks)
        self.assertIn("br:r", callbacks)


class TestBrowseViewAndSecurity(unittest.IsolatedAsyncioTestCase):
    async def test_browse_outside_root_blocked(self):
        mock_update = MagicMock()
        mock_update.callback_query = MagicMock()
        mock_update.callback_query.answer = AsyncMock()
        mock_context = MagicMock()
        mock_context.user_data = {}

        with patch.object(drive_service.default_drive_service, "is_within_managed_root", return_value=False):
            await handlers.render_browse_view(mock_update, mock_context, "rogue_folder_id")

        mock_update.callback_query.answer.assert_awaited_once()
        self.assertIn("outside the managed Google Drive", mock_update.callback_query.answer.call_args[0][0])


class TestFileDetailsAndSendBack(unittest.IsolatedAsyncioTestCase):
    async def test_file_details_renders_metadata(self):
        mock_update = MagicMock()
        mock_query = MagicMock()
        mock_query.message.edit_text = AsyncMock()
        mock_update.callback_query = mock_query
        mock_context = MagicMock()

        file_meta = {
            "id": "file_123",
            "name": "sample.pdf",
            "size": "2097152",
            "mimeType": "application/pdf",
            "createdTime": "2026-10-07T10:00:00Z",
            "parents": ["parent_folder"],
            "webViewLink": "https://drive.google.com/sample",
        }

        with patch.object(drive_service.default_drive_service, "is_within_managed_root", return_value=True), \
             patch.object(drive_service.default_drive_service, "get_metadata", return_value=file_meta):
            await handlers.render_file_details(mock_update, mock_context, "file_123")

        mock_query.message.edit_text.assert_awaited_once()
        sent_text = mock_query.message.edit_text.call_args[0][0]
        self.assertIn("sample.pdf", sent_text)
        self.assertIn("2.0 MB", sent_text)
        self.assertIn("application/pdf", sent_text)

    async def test_send_file_exceeding_50mb_shows_link_only(self):
        mock_update = MagicMock()
        mock_message = MagicMock()
        mock_message.reply_text = AsyncMock()
        mock_update.callback_query.message = mock_message
        mock_context = MagicMock()

        # 60 MB file
        file_meta = {
            "id": "large_file",
            "name": "large_video.mp4",
            "size": str(60 * 1024 * 1024),
            "webViewLink": "https://drive.google.com/large",
        }

        with patch.object(drive_service.default_drive_service, "is_within_managed_root", return_value=True), \
             patch.object(drive_service.default_drive_service, "get_metadata", return_value=file_meta), \
             patch.object(drive_service.default_drive_service, "download_file") as mock_download:
            await handlers.execute_send_file(mock_update, mock_context, "large_file")

            # Should NOT download file
            mock_download.assert_not_called()
            mock_message.reply_text.assert_awaited_once()
            sent_text = mock_message.reply_text.call_args[0][0]
            self.assertIn("larger than Telegram's send limit", sent_text)
            self.assertIn("60.0 MB", sent_text)

    async def test_send_file_under_50mb_downloads_and_sends(self):
        mock_update = MagicMock()
        mock_status = MagicMock()
        mock_status.edit_text = AsyncMock()
        mock_status.delete = AsyncMock()
        mock_message = MagicMock()
        mock_message.reply_text = AsyncMock(return_value=mock_status)
        mock_message.chat_id = 999
        mock_update.callback_query.message = mock_message

        mock_context = MagicMock()
        mock_context.bot.send_document = AsyncMock()

        # 5 MB file
        file_meta = {
            "id": "small_file",
            "name": "small_doc.pdf",
            "size": str(5 * 1024 * 1024),
            "webViewLink": "https://drive.google.com/small",
        }

        def fake_download(file_id, destination_path):
            with open(destination_path, "w") as f:
                f.write("content")
            return destination_path

        with patch.object(drive_service.default_drive_service, "is_within_managed_root", return_value=True), \
             patch.object(drive_service.default_drive_service, "get_metadata", return_value=file_meta), \
             patch.object(drive_service.default_drive_service, "download_file", side_effect=fake_download):
            await handlers.execute_send_file(mock_update, mock_context, "small_file")

            mock_context.bot.send_document.assert_awaited_once()
            # Verify temp file was cleaned up in downloads/
            temp_files = list(config.DOWNLOADS_DIR.glob("send_*_small_doc.pdf"))
            self.assertEqual(len(temp_files), 0, "Temporary send file was not cleaned up!")


class TestSearch(unittest.IsolatedAsyncioTestCase):
    async def test_search_results_display(self):
        mock_update = MagicMock()
        mock_status = MagicMock()
        mock_status.edit_text = AsyncMock()
        mock_update.effective_message.reply_text = AsyncMock(return_value=mock_status)
        mock_context = MagicMock()

        search_results = [
            {"id": "sr_1", "name": "Banking_Syllabus.pdf", "size": 1024 * 1024},
            {"id": "sr_2", "name": "Banking_Current_Affairs.pdf", "size": 2048 * 1024},
        ]

        with patch.object(drive_service.default_drive_service, "search_files", return_value=search_results):
            await handlers.execute_search(mock_update, mock_context, "Banking")

        mock_status.edit_text.assert_awaited_once()
        sent_text = mock_status.edit_text.call_args[0][0]
        self.assertIn("Search results for:", sent_text)
        self.assertIn("Banking_Syllabus.pdf", sent_text)
        self.assertIn("Banking_Current_Affairs.pdf", sent_text)


if __name__ == "__main__":
    unittest.main()
