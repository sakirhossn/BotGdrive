"""Configuration management and environment validation.

Loads and validates settings from environment variables or .env file.
Ensures zero hardcoded secrets and enforces strict user access control.
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

# Load .env if present
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
else:
    load_dotenv()

# File paths
CREDENTIALS_FILE: Path = BASE_DIR / "credentials.json"
TOKEN_FILE: Path = BASE_DIR / "token.json"
DOWNLOADS_DIR: Path = BASE_DIR / "downloads"
USER_PREFS_FILE: Path = BASE_DIR / "user_prefs.json"

# Telegram and Drive configs
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
DRIVE_FOLDER_ID: str = os.getenv("DRIVE_FOLDER_ID", "").strip()
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").strip().upper()

# Optional raw JSON strings for cloud/container deployment (e.g., Render, Railway)
CREDENTIALS_JSON_CONTENT: str = os.getenv("CREDENTIALS_JSON_CONTENT", "").strip()
TOKEN_JSON_CONTENT: str = os.getenv("TOKEN_JSON_CONTENT", "").strip()


def init_cloud_credentials() -> None:
    """Restore credentials.json and token.json from environment variables if present."""
    if CREDENTIALS_JSON_CONTENT and not CREDENTIALS_FILE.exists():
        try:
            with open(CREDENTIALS_FILE, "w", encoding="utf-8") as f:
                f.write(CREDENTIALS_JSON_CONTENT)
            logging.getLogger(__name__).info("Wrote credentials.json from CREDENTIALS_JSON_CONTENT env var.")
        except OSError as exc:
            logging.getLogger(__name__).error("Failed to write credentials.json from env: %s", exc)

    if TOKEN_JSON_CONTENT and not TOKEN_FILE.exists():
        try:
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(TOKEN_JSON_CONTENT)
            logging.getLogger(__name__).info("Wrote token.json from TOKEN_JSON_CONTENT env var.")
        except OSError as exc:
            logging.getLogger(__name__).error("Failed to write token.json from env: %s", exc)


def parse_allowed_user_ids(raw_ids: str | None = None) -> Set[int]:
    """Parse comma-separated list of allowed numeric Telegram user IDs."""
    if raw_ids is None:
        raw_ids = os.getenv("ALLOWED_USER_IDS", "")

    allowed: Set[int] = set()
    for item in raw_ids.split(","):
        cleaned = item.strip()
        if cleaned:
            try:
                allowed.add(int(cleaned))
            except ValueError:
                logging.getLogger(__name__).warning("Invalid user ID in ALLOWED_USER_IDS: '%s'", cleaned)
    return allowed


ALLOWED_USER_IDS: Set[int] = parse_allowed_user_ids()


def validate_config(strict: bool = True) -> list[str]:
    """Validate that required environment variables are set.

    Args:
        strict: If True, raises ValueError if required variables are missing.

    Returns:
        List of missing or invalid variable descriptions.
    """
    init_cloud_credentials()
    errors: list[str] = []

    if not TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN is not set.")

    if not ALLOWED_USER_IDS:
        errors.append("ALLOWED_USER_IDS must contain at least one valid numeric Telegram user ID.")

    if not DRIVE_FOLDER_ID:
        errors.append("DRIVE_FOLDER_ID is not set.")

    # Ensure downloads directory exists
    try:
        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        errors.append(f"Cannot create downloads directory {DOWNLOADS_DIR}: {exc}")

    if strict and errors:
        raise ValueError("Configuration validation failed:\n" + "\n".join(f"- {e}" for e in errors))

    return errors


def setup_logging() -> None:
    """Configure application logging according to LOG_LEVEL."""
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Silence noisy external library debug logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("googleapiclient.discovery").setLevel(logging.WARNING)
    logging.getLogger("google.auth.transport.requests").setLevel(logging.WARNING)
