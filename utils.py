"""Utility functions for formatting, file sizes, and progress reporting."""

from __future__ import annotations

import math
import re
import time
from typing import Optional


def format_file_size(size_bytes: Optional[int]) -> str:
    """Convert raw byte integer into human-readable string (KB, MB, GB)."""
    if size_bytes is None or size_bytes < 0:
        return "Unknown size"
    if size_bytes == 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    i = int(math.floor(math.log(size_bytes, 1024)))
    i = min(i, len(units) - 1)
    p = math.pow(1024, i)
    s = round(size_bytes / p, 1) if i > 0 else int(size_bytes)
    return f"{s} {units[i]}"


def format_time(seconds: float) -> str:
    """Format duration in seconds into human-readable duration."""
    seconds = int(seconds)
    if seconds <= 0:
        return "0s"
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def truncate_button_text(text: str, max_len: int = 30) -> str:
    """Truncate button label if it exceeds Telegram inline button character limit."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def sanitize_filename(filename: Optional[str], default_prefix: str = "file") -> str:
    """Sanitize filename to prevent invalid filesystem characters or traversal."""
    if not filename:
        return f"{default_prefix}_{int(time.time())}"

    clean = re.sub(r'[\\/*?:"<>|]', "_", filename)
    clean = clean.strip(". ")
    return clean or f"{default_prefix}_{int(time.time())}"


class ProgressTracker:
    """Helper to track download/upload progress with ETA and throttled updates."""

    def __init__(self, action_name: str, filename: str, total_bytes: int, update_interval: float = 3.0):
        self.action_name = action_name
        self.filename = filename
        self.total_bytes = total_bytes
        self.update_interval = update_interval
        self.start_time = time.time()
        self.last_update_time = self.start_time

    def should_update(self) -> bool:
        """Throttle updates to avoid Telegram flood limits."""
        now = time.time()
        if now - self.last_update_time >= self.update_interval:
            self.last_update_time = now
            return True
        return False

    def format_status(self, current_bytes: int) -> str:
        """Build formatted progress bar, transfer speed, and ETA message."""
        now = time.time()
        elapsed = max(now - self.start_time, 0.001)
        speed = current_bytes / elapsed
        speed_str = f"{format_file_size(int(speed))}/s"

        if self.total_bytes > 0:
            pct = min(max((current_bytes / self.total_bytes) * 100, 0), 100)
            filled = int(pct // 10)
            bar = "█" * filled + "░" * (10 - filled)

            eta_str = ""
            if speed > 0 and current_bytes < self.total_bytes:
                remaining_secs = (self.total_bytes - current_bytes) / speed
                eta_str = f" | ⏳ ETA: {format_time(remaining_secs)}"

            return (
                f"{self.action_name}: <b>{self.filename}</b>\n\n"
                f"[{bar}] {pct:.1f}%\n"
                f"📦 {format_file_size(current_bytes)} of {format_file_size(self.total_bytes)}\n"
                f"⚡ Speed: {speed_str}{eta_str}"
            )
        return (
            f"{self.action_name}: <b>{self.filename}</b>\n\n"
            f"📦 Processed: {format_file_size(current_bytes)}\n"
            f"⚡ Speed: {speed_str}"
        )
