"""Google Drive Service API integration.

Handles OAuth 2.0 authentication, token management, file/folder listing,
metadata retrieval, creation, upload, download, and moving to trash.
All blocking Google API calls should be invoked with asyncio.to_thread.
"""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

import config

logger = logging.getLogger(__name__)

# Full drive scope required to navigate, list pre-existing folders, upload, and trash.
# Note: drive.file only grants access to files created or opened by this app.
# If the root folder was created by the user in Drive UI, drive scope is required.
SCOPES: list[str] = ["https://www.googleapis.com/auth/drive"]

# In-memory cache for folder parent relationships to minimize API calls
# {folder_id: parent_id or None}
_parent_cache: Dict[str, Optional[str]] = {}


class DriveService:
    """Encapsulates Google Drive v3 API client and operations."""

    def __init__(
        self,
        credentials_file: Path = config.CREDENTIALS_FILE,
        token_file: Path = config.TOKEN_FILE,
        root_folder_id: str = config.DRIVE_FOLDER_ID,
    ) -> None:
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.root_folder_id = root_folder_id
        self._service: Optional[Resource] = None

    def authenticate(self) -> Resource:
        """Authenticate with Google OAuth 2.0 and build the Drive resource."""
        creds: Optional[Credentials] = None

        # Load cached token if available
        if self.token_file.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)
            except Exception as exc:
                logger.warning("Failed to load existing token file: %s", exc)
                creds = None

        # Refresh or initiate OAuth flow if credentials are not valid
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing expired Google OAuth access token...")
                try:
                    creds.refresh(Request())
                except Exception as exc:
                    logger.warning("Token refresh failed: %s. Initiating full OAuth flow.", exc)
                    creds = None

            if not creds:
                if not self.credentials_file.exists():
                    raise FileNotFoundError(
                        f"OAuth client secrets file not found at '{self.credentials_file}'. "
                        "Please download credentials.json from Google Cloud Console."
                    )
                logger.info("Opening browser for Google OAuth 2.0 authorization...")
                flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_file), SCOPES)
                creds = flow.run_local_server(port=0)

            # Save the refreshed/new token
            try:
                with open(self.token_file, "w", encoding="utf-8") as token_out:
                    token_out.write(creds.to_json())
                logger.info("Saved Google OAuth token to '%s'", self.token_file)
            except OSError as exc:
                logger.error("Could not write token file: %s", exc)

        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._service

    @property
    def service(self) -> Resource:
        """Get or initialize the Google Drive API service resource."""
        if self._service is None:
            self.authenticate()
        return self._service

    def get_root_folder(self) -> Dict[str, Any]:
        """Fetch metadata for the configured root folder and verify accessibility."""
        meta = self.get_metadata(self.root_folder_id)
        if meta.get("mimeType") != "application/vnd.google-apps.folder":
            raise ValueError(f"Configured DRIVE_FOLDER_ID '{self.root_folder_id}' is not a folder.")
        # Ensure root has no parent within our boundary cache
        _parent_cache[self.root_folder_id] = None
        return meta

    def get_metadata(self, file_id: str) -> Dict[str, Any]:
        """Retrieve metadata for a specific file or folder."""
        fields = "id, name, mimeType, size, modifiedTime, createdTime, parents, webViewLink, webContentLink, trashed"
        return self.service.files().get(fileId=file_id, fields=fields, supportsAllDrives=True).execute()

    def is_within_managed_root(self, file_id: str) -> bool:
        """Verify that a given file or folder is inside the managed root hierarchy.

        Climbs the parent chain up to root_folder_id. Caches parent relationships
        to minimize API round-trips.
        """
        if file_id == self.root_folder_id:
            return True

        current_id = file_id
        visited: set[str] = set()

        while current_id and current_id not in visited:
            visited.add(current_id)

            # Check cache first
            if current_id in _parent_cache:
                parent_id = _parent_cache[current_id]
                if parent_id == self.root_folder_id:
                    return True
                if parent_id is None:
                    # Root or orphaned
                    return current_id == self.root_folder_id
                current_id = parent_id
                continue

            try:
                meta = self.get_metadata(current_id)
                parents = meta.get("parents", [])
                if not parents:
                    _parent_cache[current_id] = None
                    return current_id == self.root_folder_id

                parent_id = parents[0]
                _parent_cache[current_id] = parent_id

                if parent_id == self.root_folder_id:
                    return True
                current_id = parent_id
            except Exception as exc:
                logger.warning("Error inspecting hierarchy for id=%s: %s", current_id, exc)
                return False

        return False

    def list_children(self, folder_id: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """List folders and files directly contained in a folder.

        Returns:
            (folders, files)
        """
        query = f"'{folder_id}' in parents and trashed = false"
        fields = "files(id, name, mimeType, size, modifiedTime, createdTime, parents, webViewLink, trashed)"

        items: List[Dict[str, Any]] = []
        page_token: Optional[str] = None

        while True:
            response = self.service.files().list(
                q=query,
                spaces="drive",
                fields=f"nextPageToken, {fields}",
                pageToken=page_token,
                pageSize=100,
                orderBy="name_natural",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()

            items.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        folders: List[Dict[str, Any]] = []
        files: List[Dict[str, Any]] = []

        for item in items:
            # Update parent cache
            _parent_cache[item["id"]] = folder_id
            if item.get("mimeType") == "application/vnd.google-apps.folder":
                folders.append(item)
            else:
                files.append(item)

        return folders, files

    def list_folders(self, folder_id: str) -> List[Dict[str, Any]]:
        """List subfolders within a folder."""
        folders, _ = self.list_children(folder_id)
        return folders

    def list_files(self, folder_id: str) -> List[Dict[str, Any]]:
        """List files within a folder."""
        _, files = self.list_children(folder_id)
        return files

    def search_files(self, keyword: str, folder_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search files by name within the managed hierarchy.

        Args:
            keyword: Substring to search in filenames.
            folder_id: Target folder or defaults to root_folder_id.
        """
        target_root = folder_id or self.root_folder_id
        # Escape single quotes in keyword
        safe_keyword = keyword.replace("'", "\\'")
        query = f"name contains '{safe_keyword}' and mimeType != 'application/vnd.google-apps.folder' and trashed = false"
        fields = "files(id, name, mimeType, size, modifiedTime, createdTime, parents, webViewLink, trashed)"

        response = self.service.files().list(
            q=query,
            spaces="drive",
            fields=fields,
            pageSize=50,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()

        raw_files = response.get("files", [])
        # Filter only items within managed root hierarchy
        matched: List[Dict[str, Any]] = []
        for f in raw_files:
            if self.is_within_managed_root(f["id"]):
                matched.append(f)
        return matched

    def create_folder(self, name: str, parent_id: str) -> Dict[str, Any]:
        """Create a new folder inside parent_id.

        Validates that parent_id belongs to the managed hierarchy.
        """
        if not self.is_within_managed_root(parent_id):
            raise PermissionError("Cannot create folder outside the managed root hierarchy.")

        body = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        folder = self.service.files().create(
            body=body,
            fields="id, name, mimeType, parents, webViewLink",
            supportsAllDrives=True,
        ).execute()

        _parent_cache[folder["id"]] = parent_id
        logger.info("Created folder '%s' (id=%s) inside parent=%s", name, folder["id"], parent_id)
        return folder

    def upload_file(
        self,
        local_path: str | Path,
        filename: str,
        mime_type: Optional[str] = None,
        folder_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload a local file to Google Drive.

        Args:
            local_path: Path to the local file.
            filename: Desired Drive filename.
            mime_type: MIME type of the file.
            folder_id: Destination folder ID (defaults to root_folder_id).
        """
        target_folder = folder_id or self.root_folder_id
        if not self.is_within_managed_root(target_folder):
            raise PermissionError("Cannot upload to a folder outside the managed root hierarchy.")

        file_metadata = {
            "name": filename,
            "parents": [target_folder],
        }

        media = MediaFileUpload(
            str(local_path),
            mimetype=mime_type or "application/octet-stream",
            resumable=True,
        )

        uploaded = self.service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, size, mimeType, webViewLink, parents",
            supportsAllDrives=True,
        ).execute()

        _parent_cache[uploaded["id"]] = target_folder
        logger.info("Uploaded file '%s' (id=%s) to folder=%s", filename, uploaded["id"], target_folder)
        return uploaded

    def download_file(self, file_id: str, destination_path: str | Path) -> Path:
        """Download a file from Google Drive to local destination.

        Verifies that file_id belongs to the managed root hierarchy.
        """
        if not self.is_within_managed_root(file_id):
            raise PermissionError("Cannot download a file outside the managed root hierarchy.")

        dest = Path(destination_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        request = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
        with open(dest, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

        logger.info("Downloaded file id=%s to '%s'", file_id, dest)
        return dest

    def trash_file(self, file_id: str) -> Dict[str, Any]:
        """Move a file or folder to Google Drive Trash.

        Never permanently deletes. Rejects if file_id is root_folder_id.
        """
        if file_id == self.root_folder_id:
            raise PermissionError("The root folder cannot be deleted.")

        if not self.is_within_managed_root(file_id):
            raise PermissionError("Cannot trash an item outside the managed root hierarchy.")

        trashed_item = self.service.files().update(
            fileId=file_id,
            body={"trashed": True},
            fields="id, name, trashed",
            supportsAllDrives=True,
        ).execute()

        _parent_cache.pop(file_id, None)
        logger.info("Moved item id=%s to trash", file_id)
        return trashed_item

    def build_drive_link(self, file_id: str) -> str:
        """Build a direct web link for viewing a file or folder in Google Drive."""
        return f"https://drive.google.com/file/d/{file_id}/view"


# Global singleton instance configured with app settings
default_drive_service = DriveService()
