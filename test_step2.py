"""Unit tests for Step 2: DriveService methods, boundary validation, and Telegram upload flow."""

import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import config
import drive_service
import handlers
import utils


class TestDriveServiceBoundaries(unittest.TestCase):
    def setUp(self):
        self.mock_service = MagicMock()
        self.drive = drive_service.DriveService(
            credentials_file=config.CREDENTIALS_FILE,
            token_file=config.TOKEN_FILE,
            root_folder_id="root_123",
        )
        self.drive._service = self.mock_service
        drive_service._parent_cache.clear()

    def test_root_is_within_root(self):
        self.assertTrue(self.drive.is_within_managed_root("root_123"))

    def test_direct_child_is_within_root(self):
        self.mock_service.files().get().execute.return_value = {
            "id": "child_456",
            "parents": ["root_123"],
        }
        self.assertTrue(self.drive.is_within_managed_root("child_456"))

    def test_nested_child_is_within_root(self):
        def get_meta(fileId, **kwargs):
            mock_call = MagicMock()
            if fileId == "nested_789":
                mock_call.execute.return_value = {"id": "nested_789", "parents": ["child_456"]}
            elif fileId == "child_456":
                mock_call.execute.return_value = {"id": "child_456", "parents": ["root_123"]}
            else:
                mock_call.execute.return_value = {"id": fileId, "parents": []}
            return mock_call

        self.mock_service.files().get.side_effect = get_meta
        self.assertTrue(self.drive.is_within_managed_root("nested_789"))

    def test_outside_hierarchy_rejected(self):
        self.mock_service.files().get().execute.return_value = {
            "id": "outside_999",
            "parents": ["other_root"],
        }
        self.assertFalse(self.drive.is_within_managed_root("outside_999"))

    def test_trash_root_prevented(self):
        with self.assertRaises(PermissionError):
            self.drive.trash_file("root_123")

    def test_create_folder_outside_root_prevented(self):
        with patch.object(self.drive, "is_within_managed_root", return_value=False):
            with self.assertRaises(PermissionError):
                self.drive.create_folder("Test", "outside_parent")

    def test_upload_outside_root_prevented(self):
        with patch.object(self.drive, "is_within_managed_root", return_value=False):
            with self.assertRaises(PermissionError):
                self.drive.upload_file("dummy.txt", "dummy.txt", folder_id="outside_parent")


class TestUploadWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_file_size_exceeding_20mb_rejected(self):
        mock_update = MagicMock()
        mock_update.effective_user.id = 12345
        mock_message = MagicMock()
        mock_update.effective_message = mock_message

        # Mock document of 25 MB
        mock_doc = MagicMock()
        mock_doc.file_id = "tg_doc_1"
        mock_doc.file_size = 25 * 1024 * 1024
        mock_doc.file_name = "huge_archive.zip"
        mock_doc.mime_type = "application/zip"
        mock_message.document = mock_doc
        mock_message.photo = None
        mock_message.video = None
        mock_message.audio = None
        mock_message.voice = None
        mock_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.user_data = {}

        with patch.object(config, "ALLOWED_USER_IDS", {12345}):
            await handlers.handle_media_upload(mock_update, mock_context)

        # Ensure reply_text called with size error
        mock_message.reply_text.assert_awaited_once()
        sent_text = mock_message.reply_text.call_args[0][0]
        self.assertIn("File too large", sent_text)
        self.assertIn("25.0 MB", sent_text)
        self.assertNotIn("pending_upload", mock_context.user_data)

    async def test_temp_file_cleanup_on_upload_error(self):
        mock_update = MagicMock()
        mock_update.effective_user.id = 12345
        mock_query = MagicMock()
        mock_update.callback_query = mock_query
        mock_status_msg = MagicMock()
        mock_status_msg.edit_text = AsyncMock()
        mock_query.message.reply_text = AsyncMock(return_value=mock_status_msg)

        mock_context = MagicMock()
        mock_context.user_data = {
            "pending_upload": {
                "file_id": "tg_123",
                "filename": "test_clean.txt",
                "file_size": 100,
                "mime_type": "text/plain",
            }
        }

        # Mock telegram file download
        mock_tg_file = MagicMock()
        async def fake_download(custom_path):
            with open(custom_path, "w") as f:
                f.write("content")
        mock_tg_file.download_to_drive = AsyncMock(side_effect=fake_download)
        mock_context.bot.get_file = AsyncMock(return_value=mock_tg_file)

        # Simulate upload error
        with patch.object(drive_service.default_drive_service, "get_metadata", return_value={"name": "TestFolder"}), \
             patch.object(drive_service.default_drive_service, "upload_file", side_effect=RuntimeError("Drive API down")):
            await handlers.execute_upload(mock_update, mock_context, "target_folder_1")

        # Verify temp files in downloads dir are cleaned up
        temp_files = list(config.DOWNLOADS_DIR.glob("temp_*_test_clean.txt"))
        self.assertEqual(len(temp_files), 0, "Temporary file was not cleaned up!")
        self.assertIn("Upload failed", mock_status_msg.edit_text.call_args[0][0])


class TestUserPreferences(unittest.TestCase):
    def test_preferences_save_and_retrieve(self):
        user_id = 987654
        folder_id = "folder_pref_123"
        folder_name = "My Uploads"

        utils.set_user_upload_pref(user_id, folder_id, folder_name)
        retrieved_id, retrieved_name = utils.get_user_upload_pref(user_id)

        self.assertEqual(retrieved_id, folder_id)
        self.assertEqual(retrieved_name, folder_name)

        # Cleanup preferences file
        if config.USER_PREFS_FILE.exists():
            config.USER_PREFS_FILE.unlink()


if __name__ == "__main__":
    unittest.main()
