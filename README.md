# BotGdrive — 2 GB Large-File Telegram to Google Drive Bot

A high-performance Telegram bot powered by Telegram's native **MTProto protocol** (via Pyrogram), enabling direct file, video, audio, and document uploads of up to **2,000 MB (2 GB)** directly to Google Drive.

Deployable to **Render Free Tier Web Services** for 24/7 background operation with **1 Gbps+ cloud fiber speeds**.

---

## 🚀 Key Advantages Over Standard Bot API

| Feature | Standard Bot API (Old) | MTProto Protocol (Upgraded) |
| :--- | :--- | :--- |
| **Max File Size** | **20 MB hard ceiling** | **2,000 MB (2 GB)** |
| **Protocol** | HTTP webhook / getUpdates | Native binary MTProto |
| **Transfer Engine** | Standard HTTP stream | C-accelerated MTProto stream (`tgcrypto`) |
| **Drive Uploads** | Resumable Upload | Resumable chunked upload (20 MB chunks) |
| **Progress Reporting** | Basic text | Real-time speed (MB/s), % bar & ETA |
| **Hosting** | Local PC | **Render Free Tier (24/7 in Cloud)** |

---

## 🛠 Architecture

```
[User on Telegram App]
         │ (Sends file up to 2,000 MB / 2 GB)
         ▼
[Telegram MTProto Servers]
         │ (High-speed MTProto binary stream)
         ▼
[Render Cloud Web Service (bot.py)]
   ├── 🌐 Stdlib HTTP Health Server (Port 10000 - Keeps Render Free Tier active)
   ├── 📥 C-accelerated Pyrogram MTProto Client
   └── 🚀 Resumable Chunked Drive Upload (20 MB chunks over 1 Gbps cloud fiber)
         ▼
[Google Drive API v3]
         │
         ▼
[Auto-cleanup local temporary file]
         │
         ▼
[Send Confirmation & Direct Drive Link to User]
```

---

## 📋 Available Commands

- `/start` — Open main interactive dashboard menu.
- `/list` or `/browse` — Interactive paginated Google Drive folder and file browser.
- `/setfolder` — Choose active destination folder for file uploads.
- `/mkdir <folder_name>` — Create a new folder in Google Drive.
- `/search <keyword>` — Search for files across Google Drive.
- `/status` — Verify bot health, MTProto status, and Drive connection.
- `/help` — Display command guide and large-file upload instructions.
- `/cancel` — Cancel any active interactive prompt.

---

## ☁️ 1-Click Render Deployment (Free Tier)

### Step 1: Push Code to GitHub
Ensure this repository is pushed to your GitHub account:
```powershell
git add .
git commit -m "Upgrade to 2 GB MTProto Bot with Render support"
git push origin main
```

### Step 2: Create Web Service on Render
1. Go to **[dashboard.render.com](https://dashboard.render.com)**.
2. Click **New +** → **Web Service**.
3. Select your GitHub repository: `sakirhossn/BotGdrive`.
4. Configure service settings:
   - **Name:** `botgdrive-2gb`
   - **Region:** Any (e.g. `Oregon` or `Frankfurt`)
   - **Branch:** `main`
   - **Runtime:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python bot.py`
   - **Instance Type:** `Free`

### Step 3: Add Environment Variables in Render
In Render's **Environment** tab, add:

| Key | Value | Description |
| :--- | :--- | :--- |
| `TELEGRAM_BOT_TOKEN` | `8949801992:AAFd...` | From @BotFather |
| `TELEGRAM_API_ID` | `35339657` | From my.telegram.org |
| `TELEGRAM_API_HASH` | `85b4961d02f3e793feb19b4b8a48b62b` | From my.telegram.org |
| `ALLOWED_USER_IDS` | `880480016` | Your Telegram User ID |
| `DRIVE_FOLDER_ID` | `1f5p2OJb_EUq0PeAJO0JvXri0zAJeTov7` | Target Drive Folder ID |
| `CREDENTIALS_JSON_CONTENT` | *(contents of credentials.json)* | Entire JSON file copied as text |
| `TOKEN_JSON_CONTENT` | *(contents of token.json)* | Entire JSON file copied as text |
| `PYTHON_VERSION` | `3.11.9` | Recommended Python version |

5. Click **Deploy Web Service**! Render will install `tgcrypto`, run the bot, pass health checks, and start streaming files up to 2 GB 24/7!

---

## 🔒 Security & Boundaries
- **Whitelist Protection**: Every incoming message and callback is strictly validated against `ALLOWED_USER_IDS`.
- **Drive Boundary Isolation**: Operations are restricted strictly within `DRIVE_FOLDER_ID`.
- **Stateless Cleanup**: Temporary chunks are purged immediately from storage once confirmed by Drive.
