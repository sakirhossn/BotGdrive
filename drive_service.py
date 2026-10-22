"""Google Drive Service API integration with High-Throughput Resumable Uploads.

Handles OAuth 2.0 authentication, token management, file/folder listing,
creation, 20 MB chunked resumable upload with live progress, and soft trashing.
"""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path
import socket
import ssl
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import httplib2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

import config

logger = logging.getLogger(__name__)

# Configure httplib2 and socket timeouts to prevent hanging sockets
httplib2.RETRIES = 3
socket.setdefaulttimeout(120)

SCOPES: list[str] = ["https://www.googleapis.com/auth/drive"]

_parent_cache: Dict[str, Optional[str]] = {}


class DriveService:
    """Encapsulates Google Drive v3 API client and operations with thread-isolated transports."""

    def __init__(
        self,
        credentials_file: Path = config.CREDENTIALS_FILE,
        token_file: Path = config.TOKEN_FILE,
        root_folder_id: str = config.DRIVE_FOLDER_ID,
    ) -> None:
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.root_folder_id = root_folder_id
        self._local = threading.local()
        self._creds_lock = threading.Lock()
        self._creds: Optional[Credentials] = None

    @property
    def _service(self) -> Optional[Resource]:
        """Backward-compatible accessor for thread-local service."""
        return getattr(self._local, "service", None)

    @_service.setter
    def _service(self, val: Optional[Resource]) -> None:
        self._local.service = val

    def _get_credentials(self) -> Credentials:
        """Thread-safe retrieval and refresh of Google OAuth 2.0 credentials."""
        with self._creds_lock:
            if self._creds and self._creds.valid:
                return self._creds

            config.restore_cloud_credentials()
            creds: Optional[Credentials] = self._creds

            if creds is None and self.token_file.exists():
                try:
                    creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)
                except Exception as exc:
                    logger.warning("Failed to load existing token file: %s", exc)
                    creds = None

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
                            "Please provide credentials.json or set CREDENTIALS_JSON_CONTENT in environment."
                        )
                    logger.info("Initiating Google OAuth flow...")
                    flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_file), SCOPES)
                    try:
                        creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
                    except Exception as oauth_err:
                        err_msg = str(oauth_err)
                        if "browser" in err_msg.lower() or os.getenv("RENDER"):
                            raise RuntimeError(
                                "Google OAuth token is expired or revoked ('invalid_grant'), and this server cannot open a browser (headless cloud environment).\n"
                                "👉 FIX: Re-authenticate locally on your PC by running 'python generate_token.py', then paste the new token into Render's 'TOKEN_JSON_CONTENT' environment variable."
                            ) from oauth_err
                        raise

                    try:
                        with open(self.token_file, "w", encoding="utf-8") as token_out:
                            token_out.write(creds.to_json())
                        logger.info("Saved Google OAuth token to '%s'", self.token_file)
                    except OSError as exc:
                        logger.error("Could not write token file: %s", exc)

            self._creds = creds
            return self._creds

    def authenticate(self) -> Resource:
        """Authenticate with Google OAuth 2.0 and build the Drive resource for the current thread."""
        creds = self._get_credentials()
        self._local.service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._local.service

    @property
    def service(self) -> Resource:
        """Get or initialize thread-local Google Drive API service resource."""
        svc = getattr(self._local, "service", None)
        if svc is None:
            creds = self._get_credentials()
            svc = build("drive", "v3", credentials=creds, cache_discovery=False)
            self._local.service = svc
        return svc

    def _reset_http_connections(self) -> None:
        """Clear cached httplib2 connections on current thread to prevent dead/corrupted socket reuse."""
        try:
            svc = getattr(self._local, "service", None)
            if svc is not None:
                inner = getattr(svc._http, "http", svc._http)
                if hasattr(inner, "connections"):
                    for conn in list(inner.connections.values()):
                        try:
                            conn.close()
                        except Exception:
                            pass
                    inner.connections.clear()
            self._local.service = None
            logger.debug("Cleared thread-local httplib2 connection pool for clean retry.")
        except Exception as exc:
            logger.debug("Could not reset HTTP connections: %s", exc)

    def get_root_folder(self) -> Dict[str, Any]:
        """Fetch metadata for the configured root folder and verify accessibility."""
        meta = self.get_metadata(self.root_folder_id)
        if meta.get("mimeType") != "application/vnd.google-apps.folder":
            raise ValueError(f"Configured DRIVE_FOLDER_ID '{self.root_folder_id}' is not a folder.")
        _parent_cache[self.root_folder_id] = None
        return meta

    def get_metadata(self, file_id: str) -> Dict[str, Any]:
        """Retrieve metadata for a specific file or folder."""
        fields = "id, name, mimeType, size, modifiedTime, createdTime, parents, webViewLink, webContentLink, trashed"
        return self.service.files().get(fileId=file_id, fields=fields, supportsAllDrives=True).execute()

    def is_within_managed_root(self, file_id: str) -> bool:
        """Verify that a given file or folder is inside the managed root hierarchy."""
        if file_id == self.root_folder_id:
            return True

        current_id = file_id
        visited: set[str] = set()

        while current_id and current_id not in visited:
            visited.add(current_id)

            if current_id in _parent_cache:
                parent_id = _parent_cache[current_id]
                if parent_id == self.root_folder_id:
                    return True
                if parent_id is None:
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
        """List folders and files directly contained in a folder."""
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
        """Search files by name within the managed hierarchy."""
        safe_keyword = keyword.replace("'", "\\'")
        query = f"name contains '{safe_keyword}' and mimeType != 'application/vnd.google-apps.folder' and trashed = false"
        fields = "files(id, name, mimeType, size, modifiedTime, createdTime, parents, webViewLink, trashed)"

        response = None
        for attempt in range(2):
            try:
                response = self.service.files().list(
                    q=query,
                    spaces="drive",
                    fields=fields,
                    pageSize=50,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                ).execute()
                break
            except Exception as exc:
                exc_str = str(exc).lower()
                is_conn_err = (
                    isinstance(exc, (ssl.SSLError, socket.timeout, TimeoutError, ConnectionError, OSError))
                    or "ssl" in exc_str
                    or "nonetype" in exc_str
                    or "timed out" in exc_str
                )
                if is_conn_err and attempt == 0:
                    logger.warning("Drive search_files network/SSL error: %s. Resetting connection and retrying...", exc)
                    self._reset_http_connections()
                    continue
                raise

        raw_files = response.get("files", []) if response else []
        matched: List[Dict[str, Any]] = []
        for f in raw_files:
            if self.is_within_managed_root(f["id"]):
                matched.append(f)
        return matched

    def search_folders(self, keyword: str) -> List[Dict[str, Any]]:
        """Search folders by name within the managed hierarchy."""
        safe_keyword = keyword.replace("'", "\\'")
        query = f"name contains '{safe_keyword}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        fields = "files(id, name, mimeType, parents, webViewLink, trashed)"

        response = self.service.files().list(
            q=query,
            spaces="drive",
            fields=fields,
            pageSize=50,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()

        raw_folders = response.get("files", [])
        matched: List[Dict[str, Any]] = []
        for f in raw_folders:
            if self.is_within_managed_root(f["id"]):
                matched.append(f)
        return matched

    def get_folder_tree(self, root_id: Optional[str] = None, max_depth: int = 4) -> List[Dict[str, Any]]:
        """Traverse folder hierarchy up to max_depth and return list of folders with depth level."""
        target_root = root_id or self.root_folder_id
        root_meta = self.get_metadata(target_root)

        tree: List[Dict[str, Any]] = [
            {"id": target_root, "name": root_meta.get("name", "Root"), "depth": 0, "parent": None}
        ]

        visited = {target_root}

        def _walk(parent_id: str, depth: int):
            if depth >= max_depth:
                return
            subfolders = self.list_folders(parent_id)
            for sf in subfolders:
                sf_id = sf["id"]
                if sf_id in visited:
                    continue
                visited.add(sf_id)
                tree.append({
                    "id": sf_id,
                    "name": sf.get("name", "Untitled Folder"),
                    "depth": depth + 1,
                    "parent": parent_id,
                    "webViewLink": sf.get("webViewLink"),
                })
                _walk(sf_id, depth + 1)

        _walk(target_root, 0)
        return tree

    def get_parent_folder(self, folder_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve the parent folder within the managed root hierarchy."""
        if not folder_id or folder_id == self.root_folder_id:
            return None
        try:
            meta = self.get_metadata(folder_id)
            parents = meta.get("parents", [])
            if not parents:
                return None
            parent_id = parents[0]
            if not self.is_within_managed_root(parent_id):
                return None
            parent_meta = self.get_metadata(parent_id)
            return {"id": parent_id, "name": parent_meta.get("name", "Parent Folder")}
        except Exception as exc:
            logger.debug("Could not resolve parent for folder %s: %s", folder_id, exc)
            return None

    def get_folder_breadcrumbs(self, folder_id: str) -> List[Dict[str, Any]]:
        """Return list of ancestor folders from root down to folder_id."""
        if not folder_id:
            root_meta = self.get_root_folder()
            return [{"id": self.root_folder_id, "name": root_meta.get("name", "Root")}]

        chain: List[Dict[str, Any]] = []
        curr_id = folder_id
        visited: set[str] = set()

        while curr_id and curr_id not in visited:
            visited.add(curr_id)
            try:
                meta = self.get_metadata(curr_id)
                name = meta.get("name", "Folder") if curr_id != self.root_folder_id else meta.get("name", "Root")
                chain.append({"id": curr_id, "name": name})
                if curr_id == self.root_folder_id:
                    break
                parents = meta.get("parents", [])
                curr_id = parents[0] if parents else None
            except Exception:
                break

        chain.reverse()
        return chain

    def create_folder(self, name: str, parent_id: str) -> Dict[str, Any]:
        """Create a new folder inside parent_id."""
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
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Any]:
        """Upload a local file to Google Drive with high-throughput 20 MB chunks.

        Args:
            local_path: Path to the local file.
            filename: Desired Drive filename.
            mime_type: MIME type of the file.
            folder_id: Destination folder ID (defaults to root_folder_id).
            progress_callback: Callable(current_bytes, total_bytes) invoked on each chunk.
        """
        target_folder = folder_id or self.root_folder_id
        if not self.is_within_managed_root(target_folder):
            raise PermissionError("Cannot upload to a folder outside the managed root hierarchy.")

        file_size = Path(local_path).stat().st_size
        file_metadata = {
            "name": filename,
            "parents": [target_folder],
        }

        # 5 MB chunk size (20 * 256 KB) optimizes throughput while staying safely below socket read timeouts
        chunk_size = 5 * 1024 * 1024
        media = MediaFileUpload(
            str(local_path),
            mimetype=mime_type or "application/octet-stream",
            chunksize=chunk_size,
            resumable=True,
        )

        request = self.service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, size, mimeType, webViewLink, parents",
            supportsAllDrives=True,
        )

        response = None
        retries = 0
        max_retries = 5
        while response is None:
            try:
                status, response = request.next_chunk(num_retries=2)
                retries = 0
            except Exception as exc:
                exc_str = str(exc).lower()
                is_conn_error = (
                    isinstance(exc, (ssl.SSLError, socket.timeout, TimeoutError, ConnectionError, OSError))
                    or "timed out" in exc_str
                    or "record layer failure" in exc_str
                    or "called a function you should not call" in exc_str
                    or ("nonetype" in exc_str and "read" in exc_str)
                )
                if is_conn_error:
                    self._reset_http_connections()
                    retries += 1
                    if retries > max_retries:
                        logger.error("Exceeded max retries (%d) during resumable upload: %s", max_retries, exc)
                        raise
                    wait_time = retries * 2
                    logger.warning("Upload chunk timeout/SSL error (%s). Resuming with fresh socket in %ds (attempt %d/%d)...", exc, wait_time, retries, max_retries)
                    time.sleep(wait_time)
                    continue
                raise
            if status and progress_callback:
                progress_callback(status.resumable_progress, file_size)

        if progress_callback:
            progress_callback(file_size, file_size)

        _parent_cache[response["id"]] = target_folder
        logger.info("Uploaded file '%s' (id=%s, size=%s) to folder=%s", filename, response["id"], file_size, target_folder)
        return response

    def download_file(
        self,
        file_id: str,
        destination_path: str | Path,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Path:
        """Download a file from Google Drive to local destination with progress support."""
        if not self.is_within_managed_root(file_id):
            raise PermissionError("Cannot download a file outside the managed root hierarchy.")

        meta = self.get_metadata(file_id)
        total_size = int(meta.get("size", 0))

        dest = Path(destination_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        with open(dest, "wb") as fh:
            request = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
            downloader = MediaIoBaseDownload(fh, request, chunksize=5 * 1024 * 1024)
            done = False
            retries = 0
            max_retries = 5
            while not done:
                try:
                    status, done = downloader.next_chunk(num_retries=2)
                    retries = 0
                except Exception as exc:
                    exc_str = str(exc).lower()
                    is_conn_error = (
                        isinstance(exc, (ssl.SSLError, socket.timeout, TimeoutError, ConnectionError, OSError))
                        or "timed out" in exc_str
                        or "record layer failure" in exc_str
                        or "called a function you should not call" in exc_str
                        or ("nonetype" in exc_str and "read" in exc_str)
                    )
                    if is_conn_error:
                        self._reset_http_connections()
                        retries += 1
                        if retries > max_retries:
                            logger.error("Exceeded max retries (%d) during Drive download: %s", max_retries, exc)
                            raise
                        wait_time = retries * 2
                        logger.warning("Drive download network/SSL error (%s). Rebuilding connection in %ds (attempt %d/%d)...", exc, wait_time, retries, max_retries)
                        time.sleep(wait_time)
                        # Re-bind request to fresh thread connection while keeping byte progress intact
                        downloader._request = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
                        continue
                    raise
                if status and progress_callback and total_size > 0:
                    progress_callback(int(status.progress() * total_size), total_size)

        if progress_callback and total_size > 0:
            progress_callback(total_size, total_size)

        logger.info("Downloaded file id=%s to '%s'", file_id, dest)
        return dest

    def trash_file(self, file_id: str) -> Dict[str, Any]:
        """Move a file or folder to Google Drive Trash (safe soft delete)."""
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
        """Build direct web link to item in Google Drive."""
        return f"https://drive.google.com/file/d/{file_id}/view"


default_drive_service = DriveService()
