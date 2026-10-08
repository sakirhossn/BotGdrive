"""Unit tests for Step 1 components: config, utils, authorization, and menus."""

import datetime
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import config
import utils


class TestConfigValidation(unittest.TestCase):
    def test_parse_allowed_user_ids(self):
        # Normal comma separated
        self.assertEqual(config.parse_allowed_user_ids("123, 456, 789"), {123, 456, 789})
        # Empty or spaces
        self.assertEqual(config.parse_allowed_user_ids(""), set())
        self.assertEqual(config.parse_allowed_user_ids("   "), set())
        # Mixed valid and invalid values
        self.assertEqual(config.parse_allowed_user_ids("123, abc, 456, "), {123, 456})

    def test_validate_config(self):
        with patch.object(config, "TELEGRAM_BOT_TOKEN", ""), \
             patch.object(config, "ALLOWED_USER_IDS", set()), \
             patch.object(config, "DRIVE_FOLDER_ID", ""):
            errors = config.validate_config(strict=False)
            self.assertEqual(len(errors), 3)

        with patch.object(config, "TELEGRAM_BOT_TOKEN", "valid_token"), \
             patch.object(config, "ALLOWED_USER_IDS", {12345}), \
             patch.object(config, "DRIVE_FOLDER_ID", "folder_abc"):
            errors = config.validate_config(strict=False)
            self.assertEqual(errors, [])


class TestUtils(unittest.TestCase):
    def test_format_file_size(self):
        self.assertEqual(utils.format_file_size(0), "0 B")
        self.assertEqual(utils.format_file_size(500), "500 B")
        self.assertEqual(utils.format_file_size(1024), "1.0 KB")
        self.assertEqual(utils.format_file_size(1048576), "1.0 MB")
        self.assertEqual(utils.format_file_size(1073741824), "1.0 GB")
        self.assertEqual(utils.format_file_size(None), "Unknown size")
        self.assertEqual(utils.format_file_size(-10), "0 B")

    def test_sanitize_filename(self):
        self.assertEqual(utils.sanitize_filename("valid_doc.pdf"), "valid_doc.pdf")
        self.assertEqual(utils.sanitize_filename("../../../etc/passwd"), "passwd")
        self.assertEqual(utils.sanitize_filename('bad:file*name?.txt'), "badfilename.txt")
        # Empty fallback generates timestamped name
        empty_res = utils.sanitize_filename("")
        self.assertTrue(empty_res.startswith("file_"))

    def test_sanitize_folder_name(self):
        # Valid folder names
        valid, name = utils.sanitize_folder_name("Invoices 2026")
        self.assertTrue(valid)
        self.assertEqual(name, "Invoices 2026")

        # Rejection of path traversal
        valid, msg = utils.sanitize_folder_name("../../secret")
        self.assertFalse(valid)
        self.assertIn("path separators", msg)

        valid, msg = utils.sanitize_folder_name("foo/bar")
        self.assertFalse(valid)

        valid, msg = utils.sanitize_folder_name("foo\\bar")
        self.assertFalse(valid)

        # Empty name
        valid, msg = utils.sanitize_folder_name("   ")
        self.assertFalse(valid)
        self.assertEqual(msg, "Folder name cannot be empty.")

    def test_truncate_button_text(self):
        self.assertEqual(utils.truncate_button_text("Short text", 20), "Short text")
        long_text = "This is a very long file name that needs truncation.pdf"
        truncated = utils.truncate_button_text(long_text, 20)
        self.assertEqual(len(truncated), 20)
        self.assertTrue(truncated.endswith("..."))

    def test_format_datetime(self):
        dt = datetime.datetime(2026, 10, 7, 22, 15)
        self.assertEqual(utils.format_datetime(dt), "07 Oct 2026, 22:15")
        iso = "2026-10-07T22:15:00Z"
        self.assertEqual(utils.format_datetime(iso), "07 Oct 2026, 22:15")
        self.assertEqual(utils.format_datetime(None), "Unknown")

    def test_paginate_items(self):
        items = list(range(25))
        # Page 1
        p1, curr, total = utils.paginate_items(items, page=1, page_size=10)
        self.assertEqual(p1, list(range(0, 10)))
        self.assertEqual(curr, 1)
        self.assertEqual(total, 3)

        # Page 3
        p3, curr, total = utils.paginate_items(items, page=3, page_size=10)
        self.assertEqual(p3, list(range(20, 25)))
        self.assertEqual(curr, 3)
        self.assertEqual(total, 3)

        # Empty
        p_empty, curr, total = utils.paginate_items([], page=1, page_size=10)
        self.assertEqual(p_empty, [])
        self.assertEqual(curr, 1)
        self.assertEqual(total, 1)

    def test_generate_media_filename(self):
        photo_name = utils.generate_media_filename("photo", "image/jpeg")
        self.assertTrue(photo_name.startswith("photo_"))
        self.assertTrue(photo_name.endswith(".jpg"))

        voice_name = utils.generate_media_filename("voice", "audio/ogg")
        self.assertTrue(voice_name.startswith("voice_"))
        self.assertTrue(voice_name.endswith(".ogg"))


class TestAuthorizationDecorator(unittest.IsolatedAsyncioTestCase):
    async def test_authorized_user(self):
        import bot

        mock_update = MagicMock()
        mock_update.effective_user.id = 11111
        mock_update.callback_query = None
        mock_context = MagicMock()

        called = False

        @bot.restricted
        async def dummy_handler(update, context):
            nonlocal called
            called = True
            return "success"

        with patch.object(config, "ALLOWED_USER_IDS", {11111}):
            res = await dummy_handler(mock_update, mock_context)
            self.assertTrue(called)
            self.assertEqual(res, "success")

    async def test_unauthorized_user_message(self):
        import bot

        mock_update = MagicMock()
        mock_update.effective_user.id = 99999
        mock_update.callback_query = None
        mock_update.effective_message.reply_text = AsyncMock()
        mock_context = MagicMock()

        called = False

        @bot.restricted
        async def dummy_handler(update, context):
            nonlocal called
            called = True

        with patch.object(config, "ALLOWED_USER_IDS", {11111}):
            await dummy_handler(mock_update, mock_context)
            self.assertFalse(called)
            mock_update.effective_message.reply_text.assert_awaited_once_with("❌ Not authorized.")

    async def test_unauthorized_user_callback(self):
        import bot

        mock_update = MagicMock()
        mock_update.effective_user.id = 99999
        mock_update.callback_query = MagicMock()
        mock_update.callback_query.answer = AsyncMock()
        mock_context = MagicMock()

        called = False

        @bot.restricted
        async def dummy_handler(update, context):
            nonlocal called
            called = True

        with patch.object(config, "ALLOWED_USER_IDS", {11111}):
            await dummy_handler(mock_update, mock_context)
            self.assertFalse(called)
            mock_update.callback_query.answer.assert_awaited_once_with("❌ Not authorized.", show_alert=True)


if __name__ == "__main__":
    unittest.main()
