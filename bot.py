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

from pyrogram import Client, filters, idle
from pyrogram.types import BotCommand

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

    # Register Command Handlers
    app.on_message(filters.command("start") & filters.private & filters.incoming)(handlers.start_command)
    app.on_message(filters.command("help") & filters.private & filters.incoming)(handlers.help_command)
    app.on_message(filters.command("status") & filters.private & filters.incoming)(handlers.status_command)
    app.on_message(filters.command(["list", "browse"]) & filters.private & filters.incoming)(handlers.list_command)
    app.on_message(filters.command("setfolder") & filters.private & filters.incoming)(handlers.setfolder_command)
    app.on_message(filters.command("mkdir") & filters.private & filters.incoming)(handlers.mkdir_command)
    app.on_message(filters.command("search") & filters.private & filters.incoming)(handlers.search_command)
    app.on_message(filters.command("cancel") & filters.private & filters.incoming)(handlers.cancel_command)

    # Register Callback Query Handler
    app.on_callback_query()(handlers.callback_handler)

    # Register Media Handler for 2 GB MTProto transfers
    app.on_message(
        (filters.document | filters.video | filters.audio | filters.photo) & filters.private & filters.incoming
    )(handlers.media_handler)

    # Register Text Handler for conversational inputs (search, mkdir name)
    app.on_message(
        filters.text
        & filters.incoming
        & ~filters.command(["start", "help", "status", "list", "browse", "setfolder", "mkdir", "search", "cancel"])
        & filters.private
    )(handlers.text_handler)

    return app


async def set_menu_commands(app: Client) -> None:
    """Register command menu autocomplete with Telegram."""
    try:
        commands = [
            BotCommand("start", "Open main dashboard menu"),
            BotCommand("list", "Browse Google Drive files and folders"),
            BotCommand("setfolder", "Select upload destination folder"),
            BotCommand("mkdir", "Create a new folder in Drive"),
            BotCommand("search", "Search for files in Drive"),
            BotCommand("status", "Check bot and Drive connection status"),
            BotCommand("help", "Show commands and guide"),
            BotCommand("cancel", "Cancel current operation"),
        ]
        await app.set_bot_commands(commands)
        logger.info("Bot command menu registered successfully.")
    except Exception as exc:
        logger.warning("Could not set bot commands: %s", exc)


def main() -> None:
    """Main entrypoint."""
    logger.info("Initializing BotGdrive (2 GB MTProto Google Drive Bot)...")

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
        logger.error("Failed to connect to Google Drive: %s", exc)
        sys.exit(1)

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
