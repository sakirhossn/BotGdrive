# 🤖 Telegram → Google Drive File Manager Bot

A production-quality, asynchronous Telegram bot in Python for personal Google Drive file and folder management. Enables seamless uploads, interactive folder browsing, hierarchy navigation, search, safe trashing, and sending Drive files directly back to Telegram.

---

## 🌟 Key Features

- **Asynchronous & Non-Blocking**: Built with `python-telegram-bot` v20+ async architecture. Synchronous Google Drive API calls run in dedicated worker threads (`asyncio.to_thread`), preventing Telegram event loop freezes.
- **Strict User Authorization**: Whitelist security (`ALLOWED_USER_IDS`). Unauthorized Telegram users cannot execute commands, upload files, or trigger callback buttons.
- **Drive Boundary Security**: Enforces single-root folder tenancy (`DRIVE_FOLDER_ID`). Every file and folder operation verifies that the target item belongs strictly to the managed hierarchy.
- **"Where Should I Upload?" Workflow**: Allows choosing destination folders on each upload, creating new folders on the fly, or remembering the last used folder.
- **Safe Deletions (Trash Only)**: Items are moved to Google Drive Trash (`trashed = true`). Permanent deletion is never executed. The root folder is strictly protected and cannot be deleted.
- **Media Support & Filename Generation**: Supports documents, high-res photos, videos, audio, and voice messages with intelligent MIME detection and timestamped filenames.
- **File Size Guards**: Rejects Telegram downloads $> 20\text{ MB}$ upfront. For files $> 50\text{ MB}$, provides a direct Drive web link instead of attempting Telegram sends.
- **Zero Temporary File Leftovers**: Staging directory `downloads/` cleans up all temporary files inside `finally` blocks.

---

## 📋 Prerequisites

- Python 3.10+ (tested on Python 3.13)
- A Telegram account
- A Google Cloud Platform (GCP) account

---

## 🚀 Setup & Installation Guide

### A. Create Your Telegram Bot
1. Open Telegram and search for [@BotFather](https://t.me/BotFather).
2. Send `/newbot`.
3. Choose a friendly name and a unique username ending in `bot` (e.g., `MyPersonalDriveBot`).
4. BotFather will provide your `TELEGRAM_BOT_TOKEN`.
5. **Never commit or share this token.**

### B. Find Your Telegram User ID
1. Open Telegram and search for [@userinfobot](https://t.me/userinfobot) or [@raw_data_bot](https://t.me/raw_data_bot).
2. Start the bot. It will reply with your numeric ID (e.g., `123456789`).
3. You will add this ID to `ALLOWED_USER_IDS` in `.env`.

### C. Configure Google Cloud Platform (OAuth 2.0)
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (e.g., `Telegram-Drive-Manager`).
3. Enable the Google Drive API:
   - Navigate to **APIs & Services** > **Library**.
   - Search for **Google Drive API** and click **Enable**.
4. Configure the OAuth Consent Screen:
   - Navigate to **APIs & Services** > **OAuth consent screen**.
   - Choose **External** user type and click **Create**.
   - Enter an App name (e.g., `Telegram Drive Bot`) and your user support email.
   - Click **Save and Continue** through Scopes.
   - Under **Test users**, click **Add Users** and enter your personal Google email address.
   - Click **Save and Continue**.
5. Create OAuth 2.0 Credentials:
   - Navigate to **APIs & Services** > **Credentials**.
   - Click **Create Credentials** > **OAuth client ID**.
   - Select Application type: **Desktop app**.
   - Enter a name (e.g., `Drive Bot Desktop Client`) and click **Create**.
   - Click **Download JSON** on the created client.
6. Rename the downloaded file to:
   ```text
   credentials.json
   ```
7. Place `credentials.json` directly in the project root directory beside `bot.py`.

> [!NOTE]
> **Why `drive` scope is used instead of `drive.file`**:
> The `drive.file` scope only grants access to files and folders created or opened by the bot itself. If you create your root folder (`DRIVE_FOLDER_ID`) or subfolders directly through the Google Drive web interface, `drive.file` will not be able to list or browse those existing folders. Using `https://www.googleapis.com/auth/drive` enables full management within your managed root folder, while our internal boundary check (`is_within_managed_root`) guarantees that the bot never accesses or modifies files outside your specified `DRIVE_FOLDER_ID`.

### D. Create the Root Drive Folder
1. Go to [Google Drive](https://drive.google.com/).
2. Create a folder to serve as the bot's root (e.g., `My Telegram Drive`).
3. Open the folder. The browser URL will look like:
   ```text
   https://drive.google.com/drive/folders/1A2B3C4D5E6F7G8H9I0J_EXAMPLE
   ```
4. Copy the alphanumeric string after `folders/` (`1A2B3C4D5E6F7G8H9I0J_EXAMPLE`).
5. This is your `DRIVE_FOLDER_ID`.

### E. Environment Configuration
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
(On Windows PowerShell: `Copy-Item .env.example .env`)

Edit `.env` with your values:
```env
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGhIJKlmNoPQRsTUVwxyZ
ALLOWED_USER_IDS=123456789
DRIVE_FOLDER_ID=1A2B3C4D5E6F7G8H9I0J_EXAMPLE
LOG_LEVEL=INFO
```
*(Multiple Telegram IDs can be comma-separated, e.g. `ALLOWED_USER_IDS=123456789,987654321`)*

### F. Installation & First Run

#### Windows (PowerShell):
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

#### Linux / macOS:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

#### First Run Authentication Flow:
1. On the first launch, a browser window will automatically open asking you to sign in with your Google account.
2. Sign in with the Google email registered under **Test Users**.
3. If Google displays "Google hasn't verified this app", click **Advanced** $\rightarrow$ **Go to Telegram Drive Bot (unsafe)**.
4. Click **Continue** to grant Google Drive permissions.
5. The local OAuth server completes the exchange, saves `token.json` automatically, and the bot starts polling Telegram.
6. Subsequent runs use `token.json` directly and automatically refresh expired tokens without prompting again.

---

## 🕹 Usage & Commands

| Command | Description |
| :--- | :--- |
| `/start` | Open the interactive main dashboard menu |
| `/list` | Browse files and folders in the current directory |
| `/folders` | Alias for `/list` to inspect folder hierarchy |
| `/mkdir <folder name>` | Create a new folder inside the current directory |
| `/search <keyword>` | Search files by name inside the managed Drive folder |
| `/delete` | Open browser to choose files or folders to move to Trash |
| `/help` | Display command guide and safety information |

### Uploading Files (Single & Multiple / Batch)
- **Single File**: Send any document, photo, video, audio, or voice note.
- **Multiple Files / Albums**: When you send several files or an album at once, the bot **asks only once**! It groups all incoming attachments into a single batch, displays a summary of the files and total size, and lets you select the destination folder with a single click.
- The bot displays:
  ```text
  📤 3 files received (Total: 4.2 MB)

  • 📄 question-paper.pdf (2.1 MB)
  • 📄 syllabus.pdf (1.3 MB)
  • 📄 notes.pdf (800 KB)

  Where should I upload them?
  [📁 Current Folder]
  [📂 Documents]
  [📂 Photos]
  [➕ Create New Folder]
  [❌ Cancel]
  ```
- Once confirmed, the bot uploads each file sequentially with live progress (`⏳ Uploading (1/3)...`), deletes all temporary files, and returns a clean completion summary with a direct link to the folder in Google Drive.
- If you have an active upload destination preference, the bot prompts with `[✅ Upload All Here]` for immediate 1-click batch upload.

### Sending Drive Files Back to Telegram
- Navigate to any file using `/list` or `/search`.
- Click the file button to view **File Details** (name, size, MIME type, upload date, folder).
- Click `[📤 Send File Here]`. If the file is $\le 50\text{ MB}$, the bot downloads it and sends it directly to your Telegram chat. If $> 50\text{ MB}$, it provides the direct Drive link.

---

## 🔒 Security Architecture

1. **Telegram Whitelist Authorization**: Every command, message handler, and callback query independently inspects `update.effective_user.id`. Unauthorized users receive `❌ Not authorized.` and execution halts immediately.
2. **Managed Hierarchy Boundary Guard (`is_within_managed_root`)**: Climbs the parent tree of any targeted file or folder up to `DRIVE_FOLDER_ID`. Prevents attackers from forging callback data to access, trash, or download arbitrary Google Drive files outside the root directory.
3. **Root Folder Protection**: The root folder cannot be deleted. Any attempt to trash `DRIVE_FOLDER_ID` is rejected at both the UI and service layer.
4. **Permanent Deletion Disabled**: Deletions exclusively move items to Google Drive Trash (`trashed = true`). Files are never permanently purged by the bot.
5. **Private by Default**: Files and folders are never made public.
6. **No Secret Leaks**: Logging is strictly sanitized. Access tokens, refresh tokens, bot tokens, and file binary contents are never logged.

---

## 🛠 Troubleshooting

| Problem | Cause & Solution |
| :--- | :--- |
| `credentials.json not found` | Download OAuth client JSON from GCP Console, rename to `credentials.json`, and place in the project root beside `bot.py`. |
| `Access blocked: authorization error (403)` | Your Google account is not added as a **Test User** in the OAuth Consent Screen. Go to GCP Console $\rightarrow$ APIs & Services $\rightarrow$ OAuth consent screen $\rightarrow$ Add Test Users. |
| `Google Drive API has not been used... (403)` | Google Drive API is disabled. Go to GCP Console $\rightarrow$ APIs & Services $\rightarrow$ Library $\rightarrow$ Google Drive API $\rightarrow$ Enable. |
| `DRIVE_FOLDER_ID is not a folder` | The ID in `.env` is either invalid or points to a file instead of a folder. Verify the ID from your Google Drive URL. |
| `❌ File too large` | Telegram Bot API allows bots to download files only up to 20 MB. Larger files must be uploaded via the Drive web UI. |
| `📦 This file is larger than Telegram's send limit` | Telegram allows bots to send documents up to 50 MB. The bot provides a direct Drive link instead. |
| `token.json` expired or invalid | Delete `token.json` and restart `python bot.py` to trigger a clean OAuth browser authorization. |

---

## 🧪 Running Unit Tests

Run the full offline test suite across all 4 stages:
```bash
python -m unittest discover -p "test_step*.py"
```
Output:
```text
Ran 35 tests in 0.242s
OK
```
