# Implementation Plan: Telegram → Google Drive File Manager Bot

A production-quality Telegram bot in Python for personal Google Drive file and folder management, adhering to strict security, boundary validation, and asynchronous best practices.

---

## 1. Architecture & Design Principles

### Core Stack
- **Language**: Python 3.10+ (Current runtime: Python 3.13)
- **Telegram Framework**: `python-telegram-bot` (v20+ async architecture with `Application`, `CommandHandler`, `MessageHandler`, `CallbackQueryHandler`, `ConversationHandler`)
- **Google Drive Client**: `google-api-python-client`, `google-auth`, `google-auth-oauthlib`
- **Configuration & Environment**: `python-dotenv`
- **Asynchronous Execution**: Google Drive API calls are blocking synchronous I/O operations; all calls will be executed via `await asyncio.to_thread(...)` to ensure the Telegram event loop remains non-blocking and responsive.

### Engineering & Philosophy (Ponytail / Minimal Complexity)
- **Standard Library First**: Utilize Python's standard library (`pathlib`, `json`, `datetime`, `mimetypes`, `os`, `logging`, `asyncio`, `re`) instead of redundant third-party dependencies.
- **Explicit Boundary Control**: Enforce single-root folder tenancy (`DRIVE_FOLDER_ID`) with recursive parent hierarchy validation (`is_within_managed_root`) before any file/folder read, write, download, or trash operation.
- **Zero Accidental Exposure**: Strict whitelist authentication on every incoming command, message, and callback query (`ALLOWED_USER_IDS`). Zero credential/token leakage in logs.
- **Fail-Safe Cleanup**: Temporary files in `downloads/` must always be purged within `finally` blocks.

---

## 2. Directory & Module Structure

```text
telegram-drive-bot/  (Workspace: d:/Coding/Agent/BotGdrive)
│
├── bot.py                  # Entrypoint, Application setup, handler registration, error handler
├── config.py               # Settings loader, env validation, authentication whitelist parsing
├── drive_service.py        # Isolated Google Drive API operations (OAuth, CRUD, metadata, boundary check)
├── handlers.py             # Telegram commands, media upload flow, inline menu navigation, callbacks
├── utils.py                # Formatting, sanitization, MIME helpers, pagination builder, button limits
│
├── requirements.txt        # Pinned dependencies
├── .env.example            # Environment template with dummy placeholders
├── .gitignore              # Ignores .env, credentials.json, token.json, downloads/, caches
├── README.md               # End-to-end setup guide, OAuth setup, troubleshooting, security notes
└── implementation_plan.md  # Detailed staged plan (this document)
```

Runtime directories and files (never committed):
```text
credentials.json            # Google OAuth 2.0 Client credentials (downloaded by user)
token.json                  # Generated OAuth user access & refresh token
downloads/                  # Temporary staging directory for downloads/uploads
user_prefs.json             # Lightweight JSON persistence for remembered upload folder
```

---

## 3. Security Architecture & Boundary Verification

### A. Telegram Authentication & Authorization
- Every handler (commands `/start`, `/list`, `/search`, `/delete`, `/folders`, `/mkdir`, media handlers, and callback queries) is protected by an authorization decorator `@restricted` or helper check:
  ```python
  if user.id not in config.ALLOWED_USER_IDS:
      await update.effective_message.reply_text("❌ Not authorized.")
      return
  ```
- Callback queries verify user identity before processing `callback_data`. Never rely on hidden keyboard buttons.

### B. Google Drive OAuth 2.0 & Token Refresh
- Authenticate using `InstalledAppFlow.from_client_secrets_file` (`credentials.json`).
- Store credentials in `token.json`. Automatically check `creds.expired` and refresh using `creds.refresh(Request())`.
- **OAuth Scope Decision**:
  - `https://www.googleapis.com/auth/drive.file` only permits access to files created or opened by the app. If the user points `DRIVE_FOLDER_ID` to an existing folder created in Google Drive web UI, `drive.file` cannot inspect pre-existing items.
  - Therefore, we support `https://www.googleapis.com/auth/drive` (or `drive.file` for strictly app-created hierarchies), with full documentation in `README.md`.
- Never make files public (no permissions insertion with `role: reader`, `type: anyone`).

### C. Managed Root Boundary Check (`is_within_managed_root`)
- Protect against ID tampering:
  - If target item equals `DRIVE_FOLDER_ID`, it is root.
  - For child items: traverse `parents` field via Drive API up to `DRIVE_FOLDER_ID`.
  - Cache folder hierarchy mappings in memory to reduce API calls while strictly verifying boundaries.
  - If an item's parent chain does not lead to `DRIVE_FOLDER_ID`, reject operation immediately.

---

## 4. Feature Flow Specifications

### Flow 1: Upload & Folder Selection
1. User sends media (Document, Photo, Video, Audio, Voice).
2. Authorization check (`ALLOWED_USER_IDS`).
3. Check Telegram download size limit (max 20 MB). If exceeded, notify user with exact size and reject.
4. Check if user has a remembered upload folder (`user_prefs.json` / state):
   - If remembered: Prompt `📤 Upload to <Folder>?` with `[✅ Upload Here]`, `[📂 Choose Another Folder]`, `[❌ Cancel]`.
   - If not set or "Choose Another Folder" clicked: Present folder navigation list showing current folder, subfolders, `[➕ Create New Folder]`, and `[❌ Cancel]`.
5. User confirms target folder.
6. Temporary download stored in `downloads/`.
7. Name & MIME detection:
   - Preserves original filename for documents.
   - Generates `photo_YYYYMMDD_HHMMSS.jpg` or `voice_YYYYMMDD_HHMMSS.ogg` for unnamed media.
   - Resolves MIME type using `mimetypes` or Telegram metadata.
8. Blocking Drive upload executed via `await asyncio.to_thread(drive_service.upload_file, ...)`.
9. Cleanup temporary file in `downloads/` inside `finally` block.
10. Return success message with name, folder, size, and web link.

### Flow 2: Browse Drive & Navigation
1. Triggered via `/list`, `/folders`, or inline button `📁 Browse Drive`.
2. Fetches subfolders and files inside current folder.
3. Sorts folders first, then files.
4. Paginates at 10 items per page with `⬅️ Previous` and `Next ➡️`.
5. Button text truncated safely (under 64 chars) with icon indicators (`📂`, `📄`).
6. Navigation controls: `[⬅️ Back]`, `[🏠 Root]`, `[🔄 Refresh]`.
7. Callback data stays strictly within Telegram's 64-byte limit using compact identifiers:
   - `f:o:<id>`: Open folder
   - `f:b:<id>`: Back to parent
   - `f:r`: Return to root
   - `fl:v:<id>`: View file details
   - `p:<page>`: Page navigation

### Flow 3: File Details & Send File Back to Telegram
1. Clicking a file button opens File Details:
   - Name, Size, MIME Type, Upload Date, Containing Folder, Drive Link.
   - Buttons: `[📤 Send File Here]`, `[🔗 Open in Drive]`, `[🗑 Delete]`, `[⬅️ Back]`.
2. If `📤 Send File Here` is pressed:
   - Verify file is within managed root hierarchy.
   - Check file size:
     - If $\le$ 50 MB: Download from Drive to `downloads/`, send to Telegram via `reply_document`, delete temporary file in `finally`.
     - If > 50 MB: Inform user that the file exceeds Telegram's 50 MB send limit and provide Drive web link.

### Flow 4: Create Folder
1. Triggered by `/mkdir <name>` or `➕ Create Folder` inline button.
2. Inline prompt asks user for folder name (using `ConversationHandler` or temporary state).
3. Sanitizes input: Reject empty names, path traversal (`..`), slashes (`/`, `\`). Treat strictly as a name.
4. Creates folder inside current viewing folder via `asyncio.to_thread(drive_service.create_folder, ...)`.
5. Supports folder creation during upload flow: immediately asks if user wants to upload pending file to the newly created folder.

### Flow 5: Search
1. Triggered via `/search <keyword>` or inline button.
2. Queries Drive API for items containing keyword where `trashed = false` within managed hierarchy.
3. Displays matching files with pagination.

### Flow 6: Safe Delete (Trash Only)
1. Triggered via `/delete` or file detail `🗑 Delete` button.
2. Never call permanent delete (`files.delete`). Always set `trashed: true` via `files.update`.
3. Strict root folder protection: If folder ID equals `DRIVE_FOLDER_ID`, reject with `❌ The root folder cannot be deleted.`
4. Explicit confirmation dialog before trashing:
   - `⚠️ Are you sure? Move <item> to Google Drive Trash?`
   - `[🗑 Yes, Delete]` and `[❌ Cancel]`.

---

## 5. Step-by-Step Staged Implementation Order

As instructed in Section 40 of the prompt, we will execute in the following exact staged sequence:

### **Stage 1: Core Foundation & Authorization**
- Files: `bot.py`, `config.py`, `utils.py`, `requirements.txt`, `.env.example`, `.gitignore`.
- Features:
  - Configuration parser and environment validation (`TELEGRAM_BOT_TOKEN`, `ALLOWED_USER_IDS`, `DRIVE_FOLDER_ID`, `LOG_LEVEL`).
  - Strict user authorization decorator/checks.
  - Commands `/start`, `/help`, and main inline dashboard menu.
  - Global error handler logging issues without revealing secrets.
  - Utility functions for size formatting, text truncation, sanitization, datetime formatting.
- Validation: Syntax compile, unit tests for config parsing, auth guards, utility functions.
- **Checkpoint**: Stop and ask user to verify Stage 1.

### **Stage 2: Google Drive Service & Upload Pipeline**
- Files: `drive_service.py`, `handlers.py` (upload logic).
- Features:
  - Google Drive OAuth 2.0 flow (`credentials.json` -> `token.json`), automatic token refresh.
  - Drive API functions: `authenticate()`, `get_metadata()`, `list_children()`, `create_folder()`, `upload_file()`, `download_file()`, `trash_file()`, `is_within_managed_root()`.
  - Telegram media handlers (document, photo, video, audio, voice).
  - 20 MB Telegram download limit enforcement.
  - Folder selection and upload workflow with remembered upload folder preference (`user_prefs.json`).
  - Temporary download management in `downloads/` with guaranteed `finally` cleanup.
  - `asyncio.to_thread` wrapping for all blocking Drive calls.
- Validation: Unit/mock tests verifying drive service methods, upload handlers, size guard, temp file cleanup.
- **Checkpoint**: Stop and ask user to verify Stage 2.

### **Stage 3: Drive Browser, File Details & Search**
- Files: Update `handlers.py`, `bot.py`.
- Features:
  - Browse Drive interface (`/list`, `/folders`, inline browser).
  - Folder navigation with hierarchy tracking, `⬅️ Back`, `🏠 Root`, and `🔄 Refresh`.
  - 10-item pagination with `⬅️ Previous` and `Next ➡️`.
  - File details view with metadata and direct Drive link.
  - Send File Here workflow with 50 MB Telegram limit check and automatic temp file deletion.
  - Search command (`/search <query>`) scoped to root hierarchy.
- Validation: Test navigation callback handlers, pagination math, size boundary check, search query construction.
- **Checkpoint**: Stop and ask user to verify Stage 3.

### **Stage 4: Safe Deletion & Boundary Protection**
- Files: Update `handlers.py`, `drive_service.py`.
- Features:
  - File and folder deletion workflow moving items strictly to Trash (`trashed: true`).
  - Two-step confirmation dialog.
  - Root folder protection (`DRIVE_FOLDER_ID` cannot be deleted).
  - Hierarchy verification preventing deletion of files outside root.
- Validation: Test delete confirmation logic, root deletion prevention test, mock trash execution.
- **Checkpoint**: Stop and ask user to verify Stage 4.

### **Stage 5: Documentation, Security Audits & Code Quality Review**
- Files: `README.md`, final codebase polish.
- Features:
  - Detailed README covering BotFather setup, Telegram User ID discovery, Google Cloud OAuth setup, `.env` configuration, virtual environment, first-run flow, testing checklist, troubleshooting guide.
  - Final security checklist audit against all 17 criteria in Section 41.
  - Run code reviews and audits (`gsd-code-review`, `ponytail-review`, `ponytail-audit`, `ponytail-gain`).
- **Checkpoint**: Deliver complete project for user deployment and verification.
