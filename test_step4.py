"""Unit tests for Step 4: Delete confirmation, trash operations, and root protection."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import config
import drive_service
import handlers


class TestDeleteOperations(unittest.IsolatedAsyncioTestCase):
    async def test_root_folder_deletion_prevented_in_prompt(self):
        mock_update = MagicMock()
        mock_query = MagicMock()
        mock_query.answer = AsyncMock()
        mock_update.callback_query = mock_query
        mock_context = MagicMock()

        with patch.object(config, "DRIVE_FOLDER_ID", "root_drive_id"):
            await handlers.prompt_delete_confirmation(mock_update, mock_context, "root_drive_id")

        mock_query.answer.assert_awaited_once_with("❌ The root folder cannot be deleted.", show_alert=True)

    async def test_root_folder_deletion_prevented_in_execute(self):
        mock_update = MagicMock()
        mock_query = MagicMock()
        mock_query.message.edit_text = AsyncMock()
        mock_update.callback_query = mock_query
        mock_context = MagicMock()

        with patch.object(config, "DRIVE_FOLDER_ID", "root_drive_id"):
            await handlers.execute_trash_item(mock_update, mock_context, "root_drive_id")

        mock_query.message.edit_text.assert_awaited_once_with("❌ The root folder cannot be deleted.")

    async def test_deletion_outside_root_prevented(self):
        mock_update = MagicMock()
        mock_query = MagicMock()
        mock_query.answer = AsyncMock()
        mock_update.callback_query = mock_query
        mock_context = MagicMock()

        with patch.object(drive_service.default_drive_service, "is_within_managed_root", return_value=False):
            await handlers.prompt_delete_confirmation(mock_update, mock_context, "outside_item_id")

        mock_query.answer.assert_awaited_once()
        self.assertIn("outside the managed Google Drive", mock_query.answer.call_args[0][0])

    async def test_file_delete_confirmation_prompt(self):
        mock_update = MagicMock()
        mock_query = MagicMock()
        mock_query.message.edit_text = AsyncMock()
        mock_update.callback_query = mock_query
        mock_context = MagicMock()

        file_meta = {
            "id": "file_to_del",
            "name": "obsolete.pdf",
            "mimeType": "application/pdf",
            "parents": ["parent_folder"],
        }

        with patch.object(drive_service.default_drive_service, "is_within_managed_root", return_value=True), \
             patch.object(drive_service.default_drive_service, "get_metadata", return_value=file_meta):
            await handlers.prompt_delete_confirmation(mock_update, mock_context, "file_to_del")

        mock_query.message.edit_text.assert_awaited_once()
        sent_text = mock_query.message.edit_text.call_args[0][0]
        self.assertIn("Are you sure?", sent_text)
        self.assertIn("obsolete.pdf", sent_text)
        self.assertIn("Google Drive Trash", sent_text)

        # Check buttons
        kb = mock_query.message.edit_text.call_args[1]["reply_markup"]
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        self.assertIn("del:yes:file_to_del", callbacks)
        self.assertIn("del:no:parent_folder", callbacks)

    async def test_folder_delete_confirmation_prompt(self):
        mock_update = MagicMock()
        mock_query = MagicMock()
        mock_query.message.edit_text = AsyncMock()
        mock_update.callback_query = mock_query
        mock_context = MagicMock()

        folder_meta = {
            "id": "subfolder_to_del",
            "name": "Old Exams",
            "mimeType": "application/vnd.google-apps.folder",
            "parents": ["parent_folder"],
        }

        with patch.object(drive_service.default_drive_service, "is_within_managed_root", return_value=True), \
             patch.object(drive_service.default_drive_service, "get_metadata", return_value=folder_meta):
            await handlers.prompt_delete_confirmation(mock_update, mock_context, "subfolder_to_del")

        mock_query.message.edit_text.assert_awaited_once()
        sent_text = mock_query.message.edit_text.call_args[0][0]
        self.assertIn("Delete folder?", sent_text)
        self.assertIn("Old Exams", sent_text)
        self.assertIn("folder and its contents will be moved to Google Drive Trash", sent_text)

    async def test_execute_trash_item_success(self):
        mock_update = MagicMock()
        mock_query = MagicMock()
        mock_query.message.edit_text = AsyncMock()
        mock_update.callback_query = mock_query
        mock_context = MagicMock()

        file_meta = {
            "id": "file_123",
            "name": "target.pdf",
            "parents": ["parent_123"],
        }

        with patch.object(drive_service.default_drive_service, "is_within_managed_root", return_value=True), \
             patch.object(drive_service.default_drive_service, "get_metadata", return_value=file_meta), \
             patch.object(drive_service.default_drive_service, "trash_file", return_value={"id": "file_123", "trashed": True}) as mock_trash:
            await handlers.execute_trash_item(mock_update, mock_context, "file_123")

            mock_trash.assert_called_once_with("file_123")
            # Verify final edit text is success
            final_call = mock_query.message.edit_text.call_args_list[-1]
            self.assertIn("Deleted", final_call[0][0])
            self.assertIn("has been moved to Google Drive Trash", final_call[0][0])


if __name__ == "__main__":
    unittest.main()
