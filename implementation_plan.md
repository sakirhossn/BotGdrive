# Implementation Plan: Universal Any-Link/Host Downloader, Menu Sync, Folder Hierarchy Navigation & Live VPS Deployment

A comprehensive architectural and implementation specification for upgrading **GDrive Upload** with:
1. **Universal Any-Link / Any-Host Downloader**: Download files and media from any website or file host (Direct links, Cloud/File hosts, 20+ social media platforms, generic HTML5/m3u8 scraper, URL shorteners, and intelligent fallback).
2. **Telegram Bot Command Menu (`/`) Sync**: Ensure all 15 commands appear in private chat when typing `/` across `BotCommandScopeAllPrivateChats` and `BotCommandScopeDefault`, plus `/reloadcommands`.
3. **Folder Hierarchy Back Navigation**: Add breadcrumbs (`Root > film > PK`), explicit `[🔙 Back to Parent]` buttons when browsing/changing upload folders, and `[🔙 Set Parent as Target]` button in `/folders` hierarchy.
4. **Oracle Ubuntu Usable Commands Reference**: Complete operational cheat sheet.
5. **Live Site Update & Git Push Procedure**: Step-by-step update on live Oracle Ubuntu.

---

## 1. Existing Architecture & Feature Inventory
- **Telegram Engine**: Pyrogram MTProto Client supporting direct 2 GB transfers.
- **Drive Service**: Google Drive API v3 with OAuth token management and resumable chunked uploads.
- **Provider Architecture**: `providers/base.py`, `providers/direct_http.py`, `providers/cloud_shares.py`, `providers/media_extractor.py`, `providers/registry.py`.
- **Command Menu**: Configured in `bot.py` via `set_menu_commands`.
- **Folder Navigation**: `handlers.py` via `build_folder_browser_keyboard`, `folders_command`, and `setfolder_command`.

---

## 2. Universal Any-Link / Any-Host Downloader Architecture

To support **any link or file host**, the downloader is structured into 4 cascading resolution tiers:

```
                              User Sends Any URL
                                      │
                                      ▼
                        SSRF & Security Validation
                      (Rejects loopback, private IPs)
                                      │
                                      ▼
             ┌─────────────────────────────────────────────────┐
             │ Tier 1: Specialized Cloud & File Host Resolvers │
             └────────────────────────┬────────────────────────┘
                                      │ (If GDrive, Dropbox, Pixeldrain,
                                      │  Mediafire, Catbox, GitHub, etc.)
                                      ├─────────────────────────► Normalize to direct stream
                                      │
                                      ▼ (Otherwise)
             ┌─────────────────────────────────────────────────┐
             │ Tier 2: Direct HTTP / Binary Stream Probe       │
             │ (Follows URL shorteners bit.ly, t.co, tinyurl)  │
             └────────────────────────┬────────────────────────┘
                                      │
                   Is Content-Type a downloadable binary file?
                   (e.g., zip, pdf, apk, iso, mkv, mp4, etc.)
                                     / \
                               YES  /   \  NO (Webpage text/html)
                                   ▼     ▼
         DirectHttpProvider Streams       ┌─────────────────────────────────────────────────┐
          (1 MB chunks, Range resume)     │ Tier 3: Universal Media & Stream Scraper        │
                                          │ (yt-dlp Engine + 1000+ extractors + HTML5 m3u8) │
                                          └───────────────────────┬─────────────────────────┘
                                                                  │
                                                     Does page host extractable media?
                                                                 / \
                                                           YES  /   \  NO
                                                               ▼     ▼
                                                 MediaExtractorProvider   ┌────────────────────────────────┐
                                                 streams audio/video/reel │ Tier 4: Diagnostic Explanation │
                                                 (with ffmpeg fallback)   │ (Explains why link cannot be   │
                                                                          │  downloaded: login/DRM/text)   │
                                                                          └────────────────────────────────┘
```

### Tier 1: Cloud & File-Host Resolvers (`providers/cloud_shares.py`)
- **Google Drive**: Public file IDs converted to direct export links (`uc?export=download&id={id}`).
- **Dropbox**: Link query parameters rewritten to `dl=1`.
- **GitHub**: Releases assets & raw repository files (`raw.githubusercontent.com`).
- **Pixeldrain**: Public view links (`pixeldrain.com/u/{id}`) converted to direct download API (`pixeldrain.com/api/file/{id}`).
- **Mediafire**: Extract direct download anchor from public landing page.
- **Catbox / Litterbox**: Direct binary handling (`files.catbox.moe`, `litterbox.catbox.moe`).

### Tier 2: Universal Direct HTTP Streaming & Redirect Following (`providers/direct_http.py`)
- Resolves URL shorteners and redirects: `bit.ly`, `tinyurl.com`, `t.co`, `cutt.ly`, `is.gd`, `rb.gy`, etc.
- Re-verifies SSRF security on each redirect target.
- Emulates modern browser headers (`User-Agent: Mozilla/5.0 ...`) to bypass simple hotlink blocks.
- Detects arbitrary binary types: `.zip`, `.rar`, `.7z`, `.tar.gz`, `.iso`, `.pdf`, `.docx`, `.xlsx`, `.apk`, `.exe`, `.dmg`, `.mp4`, `.mkv`, `.mp3`, `.wav`, `.flac`, `.jpg`, `.png`, `.webp`, `.bin`, etc.
- Resumable streaming in 1 MB chunks with HTTP Range headers (`Range: bytes={offset}-`).
- 2048 MiB hard ceiling enforcement.

### Tier 3: Universal Media & Video Scraper (`providers/media_extractor.py`)
- Powered by `yt-dlp` supporting over 1,000 platforms:
  - **Social Media**: YouTube (videos, shorts, streams), Instagram (reels, posts, stories), Facebook (reels, public videos), TikTok, Twitter/X, Reddit, Threads, Pinterest (`pin.it`).
  - **Video/Audio Platforms**: Vimeo, SoundCloud, Dailymotion, Twitch, Streamable, Bilibili, Archive.org.
  - **Generic Webpage Scraper**: Extracts embedded HTML5 `<video>`, `<audio>`, HLS (`.m3u8`), and DASH streams from arbitrary websites.
- **Resilient Video Format Handling**:
  - Dynamically inspects `shutil.which("ffmpeg")`.
  - If `ffmpeg` is installed: `"bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"`.
  - If `ffmpeg` is missing: automatically falls back to `"best[ext=mp4]/best"` (avoids crashing on un-merged streams).

### Tier 4: Honest Diagnostics for Unsupported Pages
- If a link cannot be downloaded (e.g. login required, DRM protection, or text-only article), the bot provides an informative error rather than a silent failure:
  - "🔒 Private or Login-Protected Media"
  - "🛡️ DRM-Protected Content"
  - "📄 Webpage contains no extractable media or direct download link"

---

## 3. Telegram Bot Command Menu (`/`) Sync

- **Problem**: Chat menu in Telegram shows only 4 commands (`/start`, `/list`, `/folders`, `/search`), while BotFather contains 10+ commands.
- **Root Cause**: `set_bot_commands()` in `bot.py` was registered with default scope only. Telegram private chats prioritize `BotCommandScopeAllPrivateChats`.
- **Solution**:
  1. In `bot.py`, register:
     - `await app.set_bot_commands(commands, scope=BotCommandScopeDefault())`
     - `await app.set_bot_commands(commands, scope=BotCommandScopeAllPrivateChats())`
     - `await app.set_bot_commands(group_commands, scope=BotCommandScopeAllGroupChats())`
  2. Implement `/reloadcommands` command so authorized users can re-sync the command menu instantly without restarting the bot.

---

## 4. Folder Hierarchy Back Navigation

- **In `drive_service.py`**:
  - `get_parent_folder(folder_id)`: returns parent ID and parent name.
  - `get_folder_breadcrumbs(folder_id)`: returns ancestry list `[Root, ..., Current]`.
- **In `handlers.py` (`build_folder_browser_keyboard`)**:
  - Display breadcrumb path header: `📁 Path: <b>Root</b> > <b>film</b> > <b>PK</b>`
  - In folder selection mode (`nav:select_mode:` / `/setfolder`):
    - When inside a child folder (`folder_id != root_id`), display an explicit button:
      `[🔙 Back to Parent (..)]` (callback: `nav:sel_mode:{parent_id}`).
- **In `handlers.py` (`folders_command` / `act:folders`)**:
  - If the active target folder is a child folder (e.g. `NPCS` or `PK`), display:
    `[🔙 Set Parent as Target (<parent_name>)]` (callback: `sel:{parent_id}`).

---

## 5. File-by-File Changes Summary

| File | Changes |
| :--- | :--- |
| `providers/cloud_shares.py` | Add resolvers for Pixeldrain (`pixeldrain.com/u/` $\to$ `/api/file/`), Mediafire, Catbox, and Google Drive / Dropbox / GitHub. |
| `providers/media_extractor.py` | Expand recognized domains (20+ platforms); add generic extractor fallback; dynamic `ffmpeg` detection for resilient format selection. |
| `providers/registry.py` | Intelligent probe fallback: when Direct HTTP detects `text/html`, delegate to `yt-dlp` before declaring HTML unsupported. |
| `bot.py` | Register commands for `BotCommandScopeAllPrivateChats`, `BotCommandScopeDefault`, `BotCommandScopeAllGroupChats`; add `/reloadcommands`. |
| `drive_service.py` | Add `get_parent_folder(folder_id)` and `get_folder_breadcrumbs(folder_id)`. |
| `handlers.py` | Display breadcrumbs & `[🔙 Back to Parent]` in folder selection; add `[🔙 Set Parent as Target]` in `/folders`; add `/reloadcommands` handler; enhance temporary file cleanup to delete all stems. |
| `test_providers.py` | Unit tests for new cloud providers, redirect following, fallback media probing, and parent navigation. |
| `README.md` | Document any-link support, command menu reload, and folder navigation. |

---

## 6. Implementation Stages & Verification Checkpoints

```
Stage 1: Implementation Plan Review & Approval [CURRENT STAGE]
  └── STOP & WAIT FOR USER APPROVAL

Stage 2: Universal Any-Link / Any-Host Downloader Implementation
  ├── Add Pixeldrain & Mediafire resolvers in providers/cloud_shares.py
  ├── Expand media domains and generic scraping in providers/media_extractor.py
  ├── Add dynamic ffmpeg format resilience
  ├── Implement HTML fallback probing in providers/registry.py
  └── Enhance temporary disk cleanup in handlers.py

Stage 3: Telegram Bot Command Menu Scopes & /reloadcommands
  ├── Import BotCommandScopeAllPrivateChats & BotCommandScopeDefault in bot.py
  ├── Register scoped commands in set_menu_commands()
  └── Implement /reloadcommands handler in handlers.py & bot.py

Stage 4: Folder Hierarchy Back Navigation
  ├── Add parent & breadcrumb helpers to drive_service.py
  ├── Update build_folder_browser_keyboard with breadcrumbs and [🔙 Back to Parent]
  └── Add [🔙 Set Parent as Target] to folder hierarchy view

Stage 5: Verification & Review Commands
  ├── Run automated unit tests (test_providers.py, test_url_downloader.py)
  ├── Run /gsd-code-review
  ├── Run /ponytail, /ponytail-review, /ponytail-audit, /ponytail-gain
  └── Sync BotgDriveBig workspace

Stage 6: Git Push & Oracle Ubuntu Live Deployment Reference
  ├── Commit and push to GitHub origin main
  └── Provide Oracle Ubuntu cheat sheet and live update commands
```
