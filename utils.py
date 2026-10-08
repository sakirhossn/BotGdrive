"""Utility helpers for file formatting, validation, sanitization, and UI pagination."""

from __future__ import annotations

import datetime
import mimetypes
import re
from typing import Any, Tuple

# Pre-compile regex for folder sanitization
INVALID_FOLDER_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def format_file_size(size_bytes: int | float | None) -> str:
    """Format byte count into human-readable string (KB, MB, GB).

    Examples:
        1024 -> "1.0 KB"
        1048576 -> "1.0 MB"
    """
    if size_bytes is None:
        return "Unknown size"
    try:
        size = float(size_bytes)
    except (ValueError, TypeError):
        return "Unknown size"

    if size < 0:
        return "0 B"
    if size < 1024:
        return f"{int(size)} B"

    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1024.0
        if size < 1024.0 or unit == "TB":
            return f"{size:.1f} {unit}"
    return f"{size:.1f} TB"


def sanitize_filename(name: str | None, default_prefix: str = "file") -> str:
    """Sanitize a filename, stripping directory traversal or unsafe path characters."""
    if not name or not name.strip():
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"{default_prefix}_{timestamp}"

    # Extract base name to defeat directory traversal
    import os
    clean = os.path.basename(name.replace("\\", "/").rstrip("/"))
    # Strip dangerous characters
    clean = re.sub(r'[:*?"<>|\x00-\x1f]', "", clean)
    # Collapse multiple dots or leading dots that might hide files or traverse
    clean = clean.lstrip(".")
    if not clean:
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"{default_prefix}_{timestamp}"
    return clean[:255]


def sanitize_folder_name(name: str | None) -> Tuple[bool, str]:
    """Validate and sanitize a folder name.

    Returns:
        (is_valid, cleaned_name_or_error_message)
    """
    if not name or not name.strip():
        return False, "Folder name cannot be empty."

    raw = name.strip()

    # Reject path traversal attempts
    if ".." in raw or "/" in raw or "\\" in raw:
        return False, "Folder name cannot contain path separators ('/', '\\') or '..'."

    # Strip illegal characters
    cleaned = INVALID_FOLDER_CHARS.sub("", raw).strip()
    if not cleaned or cleaned.startswith("."):
        return False, "Folder name contains invalid characters."

    if len(cleaned) > 100:
        return False, "Folder name cannot exceed 100 characters."

    return True, cleaned


def truncate_button_text(text: str, max_length: int = 32) -> str:
    """Safely truncate text for Telegram inline button labels."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def format_datetime(iso_str_or_dt: str | datetime.datetime | None) -> str:
    """Format ISO 8601 string or datetime into a readable format.

    Example: "07 Oct 2026, 22:15"
    """
    if not iso_str_or_dt:
        return "Unknown"

    dt: datetime.datetime | None = None
    if isinstance(iso_str_or_dt, datetime.datetime):
        dt = iso_str_or_dt
    elif isinstance(iso_str_or_dt, str):
        try:
            # Handle ISO string with potential Z
            cleaned_str = iso_str_or_dt.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(cleaned_str)
        except Exception:
            return iso_str_or_dt

    if dt:
        return dt.strftime("%d %b %Y, %H:%M")
    return "Unknown"


def get_extension_from_mime(mime_type: str | None) -> str:
    """Determine file extension from MIME type with sensible fallbacks."""
    if not mime_type:
        return ""

    # Common overrides for Telegram types
    overrides = {
        "audio/ogg": ".ogg",
        "audio/opus": ".opus",
        "video/mp4": ".mp4",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "application/pdf": ".pdf",
    }
    lowered = mime_type.lower()
    if lowered in overrides:
        return overrides[lowered]

    ext = mimetypes.guess_extension(mime_type)
    if ext:
        if ext == ".jpe":
            return ".jpg"
        return ext
    return ""


def generate_media_filename(media_type: str, mime_type: str | None = None) -> str:
    """Generate timestamped filename for media lacking original filename.

    Example: photo_20261007_221530.jpg, voice_20261007_221530.ogg
    """
    now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = get_extension_from_mime(mime_type)
    if not ext:
        if media_type == "photo":
            ext = ".jpg"
        elif media_type == "voice":
            ext = ".ogg"
        elif media_type == "video":
            ext = ".mp4"
        elif media_type == "audio":
            ext = ".mp3"
        else:
            ext = ".bin"
    return f"{media_type}_{now}{ext}"


def paginate_items(items: list[Any], page: int = 1, page_size: int = 10) -> Tuple[list[Any], int, int]:
    """Slice items for pagination.

    Returns:
        (paged_items, current_page, total_pages)
    """
    if not items:
        return [], 1, 1

    total_items = len(items)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))

    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end], page, total_pages


def get_user_upload_pref(user_id: int) -> Tuple[str | None, str | None]:
    """Retrieve remembered upload folder ID and name for a user.

    Returns:
        (folder_id, folder_name) or (None, None)
    """
    import json
    from config import USER_PREFS_FILE

    if not USER_PREFS_FILE.exists():
        return None, None
    try:
        with open(USER_PREFS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            user_data = data.get(str(user_id), {})
            return user_data.get("folder_id"), user_data.get("folder_name")
    except Exception:
        return None, None


def set_user_upload_pref(user_id: int, folder_id: str, folder_name: str) -> None:
    """Persist remembered upload folder ID and name for a user."""
    import json
    from config import USER_PREFS_FILE

    data: dict[str, Any] = {}
    if USER_PREFS_FILE.exists():
        try:
            with open(USER_PREFS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

    data[str(user_id)] = {
        "folder_id": folder_id,
        "folder_name": folder_name,
    }

    try:
        with open(USER_PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

