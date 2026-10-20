"""Main application entrypoint for BotGdrive (2 GB MTProto Bot).

Runs Pyrogram MTProto client alongside a lightweight stdlib HTTP health server
for 100% Free Tier Render Web Service deployment.
"""

from __future__ import annotations

import asyncio
import http.server
import logging
import os
import sys
import threading

# Python 3.14+ / Asyncio safety patch: Ensure event loop exists before importing Pyrogram
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

# Backward compatibility polyfill for Python 3.8 (<3.9 lacks asyncio.to_thread)
if not hasattr(asyncio, "to_thread"):
    import functools

    async def _compat_to_thread(func, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))

    asyncio.to_thread = _compat_to_thread

from pyrogram import Client, filters, idle
from pyrogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
)

import config
import drive_service
import handlers

logger = logging.getLogger(__name__)


class HealthHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler to satisfy Render Web Service health checks."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - BotGdrive 2GB Active\n")

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        # Suppress verbose HTTP access logs
        pass


def start_health_server(port: int) -> None:
    """Run lightweight HTTP health check server in background daemon thread."""
    try:
        server = http.server.ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info("Render health server active on port %s", port)
    except Exception as exc:
        logger.warning("Could not start HTTP health server on port %s: %s", port, exc)


def create_app() -> Client:
    """Build and configure the high-performance Pyrogram MTProto Client instance."""
    config.setup_logging()

    errors = config.validate_config()
    if errors:
        for err in errors:
            logger.error("Configuration error: %s", err)
        sys.exit(1)

    app = Client(
        name="bot_gdrive",
        api_id=config.TELEGRAM_API_ID,
        api_hash=config.TELEGRAM_API_HASH,
        bot_token=config.TELEGRAM_BOT_TOKEN,
        workdir=str(config.BASE_DIR),
        workers=8,
        max_concurrent_transmissions=4,
    )

    # Private Chat Commands
    app.on_message(filters.command("start") & filters.private & filters.incoming)(handlers.start_command)
    app.on_message(filters.command("help") & filters.private & filters.incoming)(handlers.help_command)
    app.on_message(filters.command("status") & filters.private & filters.incoming)(handlers.status_command)
    app.on_message(filters.command(["list", "browse"]) & filters.private & filters.incoming)(handlers.list_command)
    app.on_message(filters.command(["folders", "folder"]) & (filters.private | filters.group) & filters.incoming)(
        handlers.folders_command
    )
    app.on_message(filters.command("setfolder") & filters.private & filters.incoming)(handlers.setfolder_command)
    app.on_message(filters.command("mkdir") & filters.private & filters.incoming)(handlers.mkdir_command)
    app.on_message(filters.command("search") & filters.private & filters.incoming)(handlers.search_command)
    app.on_message(filters.command("delete") & filters.private & filters.incoming)(handlers.delete_command)
    app.on_message(filters.command(["url", "download", "dl"]) & filters.private & filters.incoming)(handlers.url_command)
    app.on_message(filters.command("settoken") & filters.private & filters.incoming)(handlers.settoken_command)
    app.on_message(filters.command(["reloadcommands", "reload_commands"]) & filters.private & filters.incoming)(
        handlers.reloadcommands_command
    )
    app.on_message(filters.command("cancel") & filters.private & filters.incoming)(handlers.cancel_command)

    # Group & Private Commands (for Admins / Whitelisted)
    app.on_message(filters.command("getfile") & (filters.private | filters.group) & filters.incoming)(handlers.getfile_command)
    app.on_message(filters.command(["link", "links"]) & (filters.private | filters.group) & filters.incoming)(handlers.link_command)
    app.on_message(filters.command(["upload", "save"]) & filters.reply & (filters.private | filters.group) & filters.incoming)(
        handlers.upload_reply_command
    )

    # Callback Queries
    app.on_callback_query()(handlers.callback_handler)

    # Media Handler for 2 GB MTProto transfers in Private Chats
    app.on_message(
        (filters.document | filters.video | filters.audio | filters.photo | filters.animation | filters.voice | filters.video_note)
        & filters.private
        & filters.incoming
    )(handlers.media_handler)

    # Text Handler for conversational inputs (search, mkdir name, pasted links)
    app.on_message(
        filters.text
        & filters.incoming
        & ~filters.command(["start", "help", "status", "list", "browse", "folders", "folder", "setfolder", "mkdir", "search", "delete", "url", "download", "dl", "settoken", "reloadcommands", "reload_commands", "cancel", "getfile", "link", "links", "upload", "save"])
        & filters.private
    )(handlers.text_handler)

    return app


async def set_menu_commands(app: Client) -> None:
    """Register command menu autocomplete with Telegram for both Default and AllPrivateChats scopes."""
    try:
        commands = [
            BotCommand("start", "🏠 Open main dashboard menu"),
            BotCommand("url", "🔗 Download file/media from public link"),
            BotCommand("download", "📥 Download from link (/download <url>)"),
            BotCommand("list", "📂 Browse Google Drive files & folders"),
            BotCommand("folders", "🗂️ View folder hierarchy & structure"),
            BotCommand("setfolder", "🎯 Select upload destination folder"),
            BotCommand("mkdir", "➕ Create a new folder in Drive"),
            BotCommand("search", "🔍 Search for files in Drive"),
            BotCommand("delete", "🗑️ Delete file or folder safely to Trash"),
            BotCommand("getfile", "📤 Deliver Drive file into chat (up to 2GB)"),
            BotCommand("link", "🌐 Share Google Drive link into chat"),
            BotCommand("links", "📑 Search & share Drive file links"),
            BotCommand("status", "📊 Check bot & Drive connection status"),
            BotCommand("reloadcommands", "🔄 Refresh Telegram bot command list"),
            BotCommand("help", "📖 Show commands and user guide"),
            BotCommand("cancel", "❌ Cancel current operation"),
        ]

        group_commands = [
            BotCommand("getfile", "📤 Deliver Drive file into chat (up to 2GB)"),
            BotCommand("link", "🌐 Share Google Drive link into chat"),
            BotCommand("links", "📑 Search & share Drive file links"),
            BotCommand("folders", "🗂️ View folder hierarchy & structure"),
            BotCommand("help", "📖 Show commands and user guide"),
        ]

        # Register across all scopes so Telegram clients display all commands in private chat
        await app.set_bot_commands(commands, scope=BotCommandScopeDefault())
        await app.set_bot_commands(commands, scope=BotCommandScopeAllPrivateChats())
        await app.set_bot_commands(group_commands, scope=BotCommandScopeAllGroupChats())
        logger.info("Bot command menu registered successfully across Default, AllPrivateChats, and AllGroupChats scopes.")
    except Exception as exc:
        logger.warning("Could not set bot commands: %s", exc)


def cleanup_downloads_dir() -> None:
    """Purge orphaned temporary files from previous runs to conserve ephemeral storage."""
    try:
        count = 0
        for item in config.DOWNLOADS_DIR.glob("*"):
            if item.is_file():
                try:
                    item.unlink(missing_ok=True)
                    count += 1
                except Exception as exc:
                    logger.warning("Could not delete orphan file %s: %s", item.name, exc)
        if count > 0:
            logger.info("Cleaned up %d orphaned temporary files from downloads directory.", count)
    except Exception as exc:
        logger.warning("Error during downloads directory cleanup: %s", exc)


def main() -> None:
    """Main entrypoint."""
    logger.info("Initializing BotGdrive (2 GB MTProto Google Drive Bot)...")

    # Purge any leftover files on ephemeral disk
    cleanup_downloads_dir()

    # Start Render health server
    start_health_server(config.PORT)

    # Pre-flight check Google Drive connection
    try:
        root_folder = drive_service.default_drive_service.get_root_folder()
        logger.info(
            "Google Drive API verified. Root folder: '%s' (ID=%s)",
            root_folder.get("name"),
            root_folder.get("id"),
        )
    except Exception as exc:
        logger.warning(
            "⚠️ Google Drive API not connected on startup: %s. "
            "Bot is starting in Safe Mode. Send /settoken in Telegram or update TOKEN_JSON_CONTENT in Render.",
            exc,
        )

    app = create_app()

    async def _runner() -> None:
        await app.start()
        try:
            await set_menu_commands(app)
            me = await app.get_me()
            logger.info("=" * 60)
            logger.info("🤖 Bot Started Successfully: @%s (%s)", me.username, me.id)
            logger.info("⚡ MTProto Protocol Active: File transfers up to 2,000 MB (2 GB)")
            logger.info("🔒 Authorized User IDs: %s", config.ALLOWED_USER_IDS)
            logger.info("=" * 60)
            await idle()
        finally:
            await app.stop()

    app.run(_runner())


if __name__ == "__main__":
    main()
