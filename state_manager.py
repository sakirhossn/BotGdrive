"""User state, session preferences, and upload folder tracking."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import config

logger = logging.getLogger(__name__)

PREFS_FILE = config.BASE_DIR / "user_prefs.json"


class StateManager:
    """Manages persistent user preferences and runtime conversational states."""

    def __init__(self, prefs_path: Path = PREFS_FILE) -> None:
        self.prefs_path = prefs_path
        self._prefs: Dict[str, Dict[str, Any]] = {}
        self._user_states: Dict[int, Dict[str, Any]] = {}
        self._load_prefs()

    def _load_prefs(self) -> None:
        if self.prefs_path.exists():
            try:
                with open(self.prefs_path, "r", encoding="utf-8") as fh:
                    self._prefs = json.load(fh)
            except Exception as exc:
                logger.warning("Could not read user prefs from %s: %s", self.prefs_path, exc)
                self._prefs = {}

    def _save_prefs(self) -> None:
        try:
            with open(self.prefs_path, "w", encoding="utf-8") as fh:
                json.dump(self._prefs, fh, indent=2)
        except Exception as exc:
            logger.error("Failed to save user prefs to %s: %s", self.prefs_path, exc)

    def get_upload_folder(self, user_id: int) -> tuple[str, str]:
        """Get (folder_id, folder_name) for a user. Defaults to root folder."""
        user_str = str(user_id)
        user_data = self._prefs.get(user_str, {})
        folder_id = user_data.get("upload_folder_id") or config.DRIVE_FOLDER_ID
        folder_name = user_data.get("upload_folder_name") or "Root"
        return folder_id, folder_name

    def set_upload_folder(self, user_id: int, folder_id: str, folder_name: str) -> None:
        """Persist selected upload destination folder for a user."""
        user_str = str(user_id)
        if user_str not in self._prefs:
            self._prefs[user_str] = {}
        self._prefs[user_str]["upload_folder_id"] = folder_id
        self._prefs[user_str]["upload_folder_name"] = folder_name
        self._save_prefs()
        logger.info("User %s set upload folder to '%s' (%s)", user_id, folder_name, folder_id)

    def set_user_action(self, user_id: int, action: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Set active interactive step for user (e.g. 'awaiting_folder_name')."""
        self._user_states[user_id] = {
            "action": action,
            "data": data or {},
        }

    def get_user_action(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve active interactive state for user."""
        return self._user_states.get(user_id)

    def clear_user_action(self, user_id: int) -> None:
        """Clear active interactive state."""
        self._user_states.pop(user_id, None)


state_manager = StateManager()
