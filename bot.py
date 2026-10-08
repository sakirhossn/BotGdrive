"""Telegram to Google Drive File Manager Bot.

Main application entrypoint, command handlers, authorization enforcement,
and global error handling.
"""

from __future__ import annotations

import functools
import html
import logging
from typing import Any, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import handlers

logger = logging.getLogger(__name__)


def restricted(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to enforce strict user whitelist authorization.

    Rejects any Telegram user ID not present in config.ALLOWED_USER_IDS.
    Independent verification applied on every message and callback query.
    """
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any) -> Any:
        user = update.effective_user
        if not user or user.id not in config.ALLOWED_USER_IDS:
            user_id_str = str(user.id) if user else "Unknown"
            logger.warning("Unauthorized access attempt blocked for user_id=%s", user_id_str)
            if update.callback_query:
                await update.callback_query.answer("❌ Not authorized.", show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text("❌ Not authorized.")
            return None
        return await func(update, context, *args, **kwargs)

    return wrapper


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Build the main dashboard inline keyboard."""
    keyboard = [
        [InlineKeyboardButton("📤 Upload File", callback_data="menu:upload")],
        [InlineKeyboardButton("📁 Browse Drive", callback_data="menu:browse")],
        [InlineKeyboardButton("📂 Choose Upload Folder", callback_data="menu:upload_folder")],
        [InlineKeyboardButton("➕ Create Folder", callback_data="menu:mkdir")],
        [InlineKeyboardButton("🔎 Search Files", callback_data="menu:search")],
        [InlineKeyboardButton("🗑 Delete", callback_data="menu:delete")],
    ]
    return InlineKeyboardMarkup(keyboard)


@restricted
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command. Displays welcome message and main dashboard menu."""
    message = (
        "☁️ <b>Google Drive Manager</b>\n\n"
        "Welcome!\n\n"
        "Choose an action:"
    )
    if update.effective_message:
        await update.effective_message.reply_text(
            message,
            reply_markup=get_main_menu_keyboard(),
            parse_mode=ParseMode.HTML,
        )


@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command. Explains all available bot features and commands."""
    help_text = (
        "📖 <b>Google Drive Manager — Help & Commands</b>\n\n"
        "<b>Available Commands:</b>\n"
        "• /start — Open the main dashboard menu\n"
        "• /list — Browse files and folders in current directory\n"
        "• /folders — View and navigate folder hierarchy\n"
        "• /mkdir &lt;name&gt; — Create a new folder\n"
        "• /search &lt;keyword&gt; — Search files inside Google Drive\n"
        "• /delete — Select files or folders to move to Trash\n"
        "• /cancel — Cancel an active operation\n"
        "• /help — Show this help message\n\n"
        "<b>File Uploads:</b>\n"
        "Send any document, photo, video, audio, or voice message directly to this chat. "
        "The bot will prompt you for the destination folder before uploading.\n\n"
        "<b>Safety Guarantee:</b>\n"
        "All deletions move items to Google Drive Trash. Files are never permanently deleted."
    )
    if update.effective_message:
        await update.effective_message.reply_text(
            help_text,
            reply_markup=get_main_menu_keyboard(),
            parse_mode=ParseMode.HTML,
        )


@restricted
async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle basic navigation callbacks from the main menu."""
    query = update.callback_query
    if not query:
        return

    data = query.data or ""
    await query.answer()

    if data == "menu:upload":
        await query.message.reply_text(
            "📤 <b>Upload File</b>\n\n"
            "Send any document, photo, video, audio, or voice message directly to this chat.\n"
            "Maximum download size limit: 20 MB.",
            parse_mode=ParseMode.HTML,
        )
    elif data == "menu:browse":
        await handlers.render_browse_view(update, context, folder_id=config.DRIVE_FOLDER_ID, page=1)
    elif data == "menu:upload_folder":
        user_id = update.effective_user.id if update.effective_user else 0
        pref_id, pref_name = utils.get_user_upload_pref(user_id)
        if pref_id and pref_name:
            curr_str = f"📁 <b>{html.escape(pref_name)}</b>"
        else:
            curr_str = "<i>(Default: Root Folder)</i>"
        await query.message.reply_text(
            f"📂 <b>Upload Folder Settings</b>\n\n"
            f"Current upload folder: {curr_str}\n\n"
            "Send any file to choose a different upload destination, or browse folders using /list.",
            parse_mode=ParseMode.HTML,
        )
    elif data == "menu:mkdir":
        context.user_data["awaiting_folder_name"] = {
            "parent_id": context.user_data.get("current_folder_id", config.DRIVE_FOLDER_ID),
            "for_upload": False,
        }
        await query.message.reply_text(
            "📁 <b>Enter the new folder name:</b>\n\n"
            "Reply with the name of the folder you want to create.",
            parse_mode=ParseMode.HTML,
        )
    elif data == "menu:search":
        context.user_data["awaiting_search_keyword"] = True
        await query.message.reply_text(
            "🔎 <b>Search Files</b>\n\n"
            "Reply with the keyword you want to search, or type <code>/search &lt;keyword&gt;</code>.",
            parse_mode=ParseMode.HTML,
        )
    elif data == "menu:delete":
        await handlers.delete_command(update, context)


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler. Logs detailed error server-side and sends friendly user message."""
    logger.error("Exception occurred while handling an update:", exc_info=context.error)

    # Do not expose technical tracebacks to users
    user_message = (
        "⚠️ <b>An unexpected error occurred.</b>\n\n"
        "The operation could not be completed. Please try again later."
    )

    if isinstance(update, Update):
        if update.callback_query:
            try:
                await update.callback_query.answer("⚠️ An error occurred. Please try again.", show_alert=True)
            except Exception:
                pass
        elif update.effective_message:
            try:
                await update.effective_message.reply_text(user_message, parse_mode=ParseMode.HTML)
            except Exception:
                pass


def build_application() -> Application:
    """Construct and configure the Telegram Application instance."""
    app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Core commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("list", handlers.browse_command))
    app.add_handler(CommandHandler("folders", handlers.browse_command))
    app.add_handler(CommandHandler("search", handlers.search_command))
    app.add_handler(CommandHandler("mkdir", handlers.mkdir_command))
    app.add_handler(CommandHandler("delete", handlers.delete_command))

    # Basic menu callbacks
    app.add_handler(CallbackQueryHandler(menu_callback_handler, pattern=r"^menu:"))

    # Media upload handlers
    media_filter = (
        filters.Document.ALL
        | filters.PHOTO
        | filters.VIDEO
        | filters.AUDIO
        | filters.VOICE
    )
    app.add_handler(MessageHandler(media_filter, handlers.handle_media_upload))

    # Upload callbacks
    app.add_handler(CallbackQueryHandler(handlers.upload_callback_handler, pattern=r"^up:"))

    # Drive Browse callbacks
    app.add_handler(CallbackQueryHandler(handlers.browse_callback_handler, pattern=r"^br:"))

    # File details & actions callbacks
    app.add_handler(CallbackQueryHandler(handlers.file_callback_handler, pattern=r"^fi:"))

    # Delete callbacks
    app.add_handler(CallbackQueryHandler(handlers.delete_callback_handler, pattern=r"^del:"))

    # Text message handler (for folder creation input and interactive prompts)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_text_message))

    # Error handling
    app.add_error_handler(global_error_handler)

    return app


def main() -> None:
    """Entrypoint to validate environment and run bot."""
    config.setup_logging()

    # Validate configuration
    validation_errors = config.validate_config(strict=False)
    if validation_errors:
        logger.error("Configuration validation failed:")
        for err in validation_errors:
            logger.error("  - %s", err)
        logger.error("Please configure the missing values in your .env file before starting.")
        return

    logger.info("Starting Telegram Drive Bot...")
    app = build_application()
    app.run_polling()


if __name__ == "__main__":
    main()
