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
- `/url <link>` — Universal Link Downloader: direct HTTP links, Cloud shares (Dropbox, Google Drive, GitHub), and public media (YouTube, Instagram Reels, Facebook Reels, TikTok, Vimeo, SoundCloud) up to 2 GB directly into Google Drive.
- `/list` or `/browse` — Interactive paginated Google Drive folder and file browser.
- `/folders` or `/folder` — Visual folder hierarchy tree.
- `/setfolder` — Choose active destination folder for file uploads.
- `/mkdir <folder_name>` — Create a new folder in Google Drive.
- `/search <keyword>` — Search for files across Google Drive.
- `/delete <keyword>` — Selectively move files or folders to Trash.
- `/getfile <keyword>` — Post file from Drive directly into chat (in private or groups).
- `/link <keyword>` — Share Google Drive link into chat.
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

## 🐧 Oracle Cloud Ubuntu — Operational Cheat Sheet & Live Updates

### 1. Daily Service Management Commands
```bash
# Check bot status and health
sudo systemctl status botgdrive

# Follow real-time live logs
sudo journalctl -u botgdrive -f

# View last 50 lines of logs
sudo journalctl -u botgdrive -n 50 --no-pager

# Restart the bot service
sudo systemctl restart botgdrive

# Stop / Start the bot service
sudo systemctl stop botgdrive
sudo systemctl start botgdrive
```

### 2. How to Update Bot on Live Ubuntu Server
```bash
# 1. Navigate to the bot repository
cd ~/BotGdrive

# 2. Stop running service
sudo systemctl stop botgdrive

# 3. Pull latest code from GitHub
git pull origin main

# 4. Activate virtual environment and update packages
source .venv/bin/activate
pip install -r requirements.txt

# 5. Ensure ffmpeg is installed for video merging
sudo apt update && sudo apt install -y ffmpeg

# 6. Restart the bot background service
sudo systemctl restart botgdrive

# 7. Check status and logs to verify it's active
sudo systemctl status botgdrive
sudo journalctl -u botgdrive -f
```

### 3. Server Monitoring & Maintenance
```bash
# Check free disk space (ensure at least 5-10 GB free for 2 GB transfers)
df -h

# Check RAM and Swap usage
free -m

# View active processes
htop
```

---

## 🔒 Security & Boundaries
- **Whitelist Protection**: Every incoming message and callback is strictly validated against `ALLOWED_USER_IDS`.
- **Drive Boundary Isolation**: Operations are restricted strictly within `DRIVE_FOLDER_ID`.
- **Stateless Cleanup**: Temporary chunks are purged immediately from storage once confirmed by Drive.
