"""Configuration management for BotGdrive (2 GB MTProto Large-File Bot).

Supports local development and Render/cloud environment credential restoration.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Set

from dotenv import load_dotenv

# Base paths
BASE_DIR: Path = Path(__file__).resolve().parent
ENV_PATH: Path = BASE_DIR / ".env"

if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
else:
    load_dotenv()

# Google Drive credentials
CREDENTIALS_FILE: Path = BASE_DIR / "credentials.json"
TOKEN_FILE: Path = BASE_DIR / "token.json"
DOWNLOADS_DIR: Path = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)


def restore_cloud_credentials() -> None:
    """Auto-restore credentials from environment variables for Render/cloud deployment."""
    creds_content = os.getenv("CREDENTIALS_JSON_CONTENT", "").strip()
    if creds_content and not CREDENTIALS_FILE.exists():
        try:
            CREDENTIALS_FILE.write_text(creds_content, encoding="utf-8")
            logging.getLogger(__name__).info("Restored credentials.json from CREDENTIALS_JSON_CONTENT.")
        except Exception as exc:
            logging.getLogger(__name__).error("Failed to write credentials.json: %s", exc)

    token_content = os.getenv("TOKEN_JSON_CONTENT", "").strip()
    if token_content and not TOKEN_FILE.exists():
        try:
            TOKEN_FILE.write_text(token_content, encoding="utf-8")
            logging.getLogger(__name__).info("Restored token.json from TOKEN_JSON_CONTENT.")
        except Exception as exc:
            logging.getLogger(__name__).error("Failed to write token.json: %s", exc)


# Run credential restoration if applicable
restore_cloud_credentials()

# Telegram credentials
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_API_ID_STR: str = os.getenv("TELEGRAM_API_ID", "").strip()
TELEGRAM_API_HASH: str = os.getenv("TELEGRAM_API_HASH", "").strip()

try:
    TELEGRAM_API_ID: int = int(TELEGRAM_API_ID_STR) if TELEGRAM_API_ID_STR else 0
except ValueError:
    TELEGRAM_API_ID = 0

# Drive managed folder
DRIVE_FOLDER_ID: str = os.getenv("DRIVE_FOLDER_ID", "").strip()
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").strip().upper()

# Render Web Service HTTP Port
PORT: int = int(os.getenv("PORT", "10000"))


def parse_allowed_user_ids(raw_ids: str) -> Set[int]:
    """Parse comma-separated Telegram user IDs."""
    ids: Set[int] = set()
    for item in raw_ids.split(","):
        cleaned = item.strip()
        if not cleaned:
            continue
        try:
            ids.add(int(cleaned))
        except ValueError:
            logging.getLogger(__name__).warning("Invalid user ID in ALLOWED_USER_IDS: '%s'", cleaned)
    return ids


ALLOWED_USER_IDS: Set[int] = parse_allowed_user_ids(os.getenv("ALLOWED_USER_IDS", ""))


def setup_logging() -> None:
    """Initialize structured application logging."""
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("pyrogram").setLevel(logging.WARNING)


def validate_config() -> list[str]:
    """Check required environment settings."""
    errors: list[str] = []
    if not TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN is missing or empty in .env")
    if not TELEGRAM_API_ID:
        errors.append("TELEGRAM_API_ID is missing or invalid in .env (get it from my.telegram.org/apps)")
    if not TELEGRAM_API_HASH:
        errors.append("TELEGRAM_API_HASH is missing or empty in .env (get it from my.telegram.org/apps)")
    if not DRIVE_FOLDER_ID:
        errors.append("DRIVE_FOLDER_ID is missing or empty in .env")
    if not ALLOWED_USER_IDS:
        errors.append("ALLOWED_USER_IDS is missing or empty in .env")
    return errors
