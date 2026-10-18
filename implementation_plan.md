# Implementation Plan: Universal Link Downloader for GDrive Upload

A comprehensive architectural and implementation specification for upgrading **GDrive Upload** into a modular, production-grade universal link downloader and Google Drive uploader, preserving all existing 2 GB MTProto and Drive management features.

---

## 1. Existing Architecture & Feature Inventory

### Current System Overview
- **Telegram Protocol & Engine**: Uses Pyrogram (MTProto) for direct binary file uploads up to **2,000 MB (2 GB)**, bypassing the standard 20 MB Telegram Bot API limit.
- **Hosting Environment**: Render Free Tier Web Service (`bot.py` runs a background standard-library HTTP health server on port 10000 to keep the free container active 24/7).
- **Google Drive Storage & OAuth**:
  - `drive_service.py` wraps Google Drive API v3.
  - Implements OAuth 2.0 flow with automatic token refresh and `/settoken` hot-reloading for headless cloud deployments.
  - Enforces strict boundary isolation (`is_within_managed_root`) ensuring all operations (browsing, creation, upload, trash deletion) are confined within `DRIVE_FOLDER_ID`.
  - Resumable chunked uploads using 5 MB chunks (`MediaFileUpload`).
- **State Management**:
  - `state_manager.py` persists active upload target folder in `user_prefs.json`.
  - Manages conversational actions (`awaiting_folder_name`, `awaiting_search_query`, `awaiting_url`).
- **Current URL Downloading (`url_service.py`)**:
  - Direct HTTP streaming with SSRF protection (`validate_url_security`, `SafeRedirectHandler`).
  - Rewrites public share links for Dropbox (`dl=1`), Google Drive (`uc?export=download`), and GitHub raw/releases.
  - Auto-resumes dropped streams using HTTP `Range: bytes={offset}-` in append mode (`ab`).
  - Stream buffer: 1 MB chunks; 2 GB size ceiling enforcement.
  - Limitation: Detects HTML webpages and aborts with a message; lacks modular provider dispatch and public media/video extraction.
- **Concurrency & Resource Limits**:
  - Sequential transfer lock: `TRANSFER_SEMAPHORE = asyncio.Semaphore(1)` in `handlers.py` prevents concurrent memory/disk exhaustion on Render (512 MB RAM, ~1.5 GB ephemeral disk).
  - Queue management: displays active transfer name, live progress, and queue position (`#pos`).
  - Pre-flight ephemeral disk space verification (`utils.check_disk_space`).
- **Notification & UI/UX**:
  - Real-time progress cards (`utils.ProgressTracker`) updated with `FloodWait` backoff.
  - Dual reporting: live message editing + dedicated Telegram chat text alerts (`client.send_message`) on completion and failures.

---

## 2. Proposed Architecture

```
                    ┌───────────────────────────┐
                    │  Telegram User (Private)  │
                    └─────────────┬─────────────┘
                                  │ Sends URL / Command
                                  ▼
                    ┌───────────────────────────┐
                    │  Authorization Whitelist  │
                    │   (ALLOWED_USER_IDS)      │
                    └─────────────┬─────────────┘
                                  ▼
                    ┌───────────────────────────┐
                    │   URL Link Inspector &    │
                    │      Provider Router      │
                    └─────────────┬─────────────┘
                                  │ Dispatches URL
         ┌────────────────────────┼────────────────────────┐
         ▼                        ▼                        ▼
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│ DirectHttpProvider│   │ CloudShareProvider│   │ MediaExtractor   │
│ (Direct file URLs│   │ (GDrive, Dropbox, │   │ (YouTube, Public │
│  SSRF-safe, Range│   │  GitHub Releases) │   │  Reels/Audio/Vid)│
│  1 MB chunks)    │    │                  │    │                  │
└────────┬─────────┘    └────────┬─────────┘    └────────┬─────────┘
         └────────────────────────┼────────────────────────┘
                                  ▼
                    ┌───────────────────────────┐
                    │   Interactive Link Card   │
                    │   (Check, Choose Folder,  │
                    │     Download & Upload)    │
                    └─────────────┬─────────────┘
                                  │ User Confirms
                                  ▼
                    ┌───────────────────────────┐
                    │    Transfer Queue &       │
                    │   TRANSFER_SEMAPHORE (1)  │
                    └─────────────┬─────────────┘
                                  ▼
                    ┌───────────────────────────┐
                    │ Stream to Temporary Disk  │
                    │ (2048 MiB Max, Disk Check)│
                    └─────────────┬─────────────┘
                                  ▼
                    ┌───────────────────────────┐
                    │ Google Drive API Upload   │
                    │ (Resumable 5 MB Chunks)   │
                    └─────────────┬─────────────┘
                                  ▼
                    ┌───────────────────────────┐
                    │  Auto-Purge Temp Files &  │
                    │  Send Chat Notifications  │
                    └───────────────────────────┘
```

### Architectural Principles
1. **Modular Provider Abstraction**: Replace monolithic URL logic with a pluggable `BaseProvider` interface and a centralized `ProviderRegistry`.
2. **Safe Fallbacks & Honest Reporting**: If a link is an HTML page without authorized/supported media, or protected by DRM/logins, explicitly explain the limitation rather than crashing or claiming success.
3. **Preserve Render Constraints**: Enforce strict single-transfer concurrency, 2048 MiB hard limit, disk space checks, and immediate temp-file removal to run reliably on 512 MB RAM.

---

## 3. Provider Support Matrix

| Category | Provider Module | Target URLs / Services | Method & Capabilities | Limitations / Fallbacks |
| :--- | :--- | :--- | :--- | :--- |
| **Direct HTTP/HTTPS** | `DirectHttpProvider` | Any direct link to `.mp4`, `.mkv`, `.pdf`, `.zip`, `.iso`, `.mp3`, etc. | Streamed `urllib` / `requests` with SSRF validation, HTTP Range auto-resume, 1 MB chunks. | Blocked private IPs; rejects links returning `text/html`. |
| **Cloud File Sharing** | `DropboxProvider` | `dropbox.com/s/...`, `dropbox.com/scl/...` | Rewrites parameter `dl=0` to `dl=1` for raw binary stream. | Password-protected or expired links report access error. |
| **Google Drive Public** | `GDrivePublicProvider`| `drive.google.com/file/d/{id}`, `drive.google.com/open?id={id}` | Exports via `uc?export=download&id={id}`. | Large files with virus scan confirmation bypass handled via cookie confirmation. |
| **GitHub Assets** | `GitHubProvider` | `github.com/.../releases/download/...`, `raw.githubusercontent.com/...` | Direct release binary stream with custom User-Agent. | Private repository links without auth rejected safely. |
| **Public Video & Media** | `MediaExtractorProvider` | **YouTube**: `youtube.com/watch`, `youtu.be/`, `youtube.com/shorts/`<br>**Facebook Reels**: `facebook.com/reel/`, `fb.watch/`<br>**Generic Media**: Vimeo, Soundcloud, etc. | Uses standard `yt-dlp` streaming engine to extract public media without transcoding. Streams best available MP4/M4A/MP3 directly to disk. | **Strictly authorized public content only**.<br>No DRM/paywall bypass.<br>Private, login-gated, or age-restricted videos return clear limitation explanation. |
| **Unsupported / Webpages** | `UnsupportedProvider` | Login pages, streaming portals, web articles, DRM platforms (Netflix, Spotify, etc.) | Inspects link; informs user that link is a web page or DRM-protected service. | Explains why download cannot be performed; provides original link. |

---

## 4. File-by-File Change List

| File Path | Action | Description & Scope |
| :--- | :--- | :--- |
| `config.py` | Modify | Add `MAX_URL_DOWNLOAD_MB` (default: 2048), `URL_DOWNLOAD_TIMEOUT_SECONDS` (default: 1800), and `MAX_CONCURRENT_URL_DOWNLOADS` (default: 1). Validate settings. |
| `.env.example` | Modify | Add documentation for `MAX_URL_DOWNLOAD_MB`, `URL_DOWNLOAD_TIMEOUT_SECONDS`, and `MAX_CONCURRENT_URL_DOWNLOADS`. |
| `requirements.txt` | Modify | Add `yt-dlp>=2024.0.0` for robust, authorized public media extraction (pure Python, zero binary bloat). |
| `providers/__init__.py` | Create | Package initialization exposing provider registry and types. |
| `providers/base.py` | Create | Abstract class `BaseProvider` defining `can_handle()`, `probe()`, `download()`, and `ProviderMetadata` dataclass. |
| `providers/registry.py` | Create | Registry class that matches incoming URLs against prioritized providers and returns the appropriate handler. |
| `providers/direct_http.py` | Create | Refactored direct HTTP streaming provider with SSRF validation, HTTP Range resume, 1 MB buffering, and 2 GB limit enforcement. |
| `providers/cloud_shares.py` | Create | Integrations for Dropbox, Google Drive public links, and GitHub release assets. |
| `providers/media_extractor.py` | Create | Wrapper for authorized YouTube, Facebook Reels, and public video/audio downloads via `yt-dlp`. Enforces DRM compliance and size bounds. |
| `url_service.py` | Refactor | Serve as high-level facade delegating to `providers.registry`. Preserves `probe_url`, `stream_url_to_file`, and pending task caching for backward compatibility. |
| `handlers.py` | Modify | Enhance `handle_incoming_url` and callback handlers to display provider badge, format interactive card with `[Check Link]`, `[Choose Folder]`, `[Download & Upload]`, and support rich media titles. |
| `utils.py` | Modify | Enhance MIME and format helpers for media streams and title sanitization. |
| `test_url_downloader.py` | Expand | Add comprehensive unit and mocked provider tests (direct files, YouTube URL recognition, Facebook Reels recognition, SSRF, size limits, error formatting). |
| `README.md` | Modify | Update feature matrix, commands, supported providers, environment variables, and limitations. |

---

## 5. Dependency Changes & Justification

### New Dependency: `yt-dlp>=2024.0.0`
- **Why Needed**:
  - Extracting video and audio streams from YouTube, Facebook Reels, and media hosts without a dedicated extraction engine requires reverse-engineering constantly shifting client signatures, player JavaScript, and adaptive HLS/DASH manifests.
  - `yt-dlp` is the industry-standard, pure-Python open-source tool. It has no mandatory external C dependencies and runs flawlessly on Render Free Tier (Python 3.11/Linux).
  - It provides built-in hooks for progress tracking, clean error classification (e.g. `DownloadError: Private video`, `DRM protected`), and direct-to-file streaming.
- **Why NOT Hand-Rolled Scraping**:
  - Scraping YouTube/Facebook HTML with `BeautifulSoup` or regex breaks within days due to obfuscated player scripts and rate limits.
  - `yt-dlp` respects access boundaries, handles HTTP retry/backoff automatically, and does not bypass DRM.

---

## 6. Download & Upload Workflows

### User Interaction Flow
1. **Link Detection**:
   - Authorized user sends `/url <link>` or simply pastes an `http(s)://` link in private chat.
   - Bot acknowledges: `🔍 Inspecting link...`
2. **Provider Probe**:
   - `ProviderRegistry` selects matching provider.
   - Provider fetches metadata (filename/title, file size, content type, host, thumbnail if available).
3. **Interactive Confirmation Card**:
   - Bot displays:
     ```
     🔗 Link Received
     ━━━━━━━━━━━━━━━━━━━━
     📄 Title/File: Inception_Trailer.mp4
     🌐 Source: YouTube (Public Media)
     📦 Size: ~45.2 MB
     📁 Destination: Personal_Videos
     ━━━━━━━━━━━━━━━━━━━━
     [📥 Download & Upload]
     [📂 Choose Drive Folder] [🔎 Check Link]
     [❌ Cancel]
     ```
4. **Execution & Concurrency Queue**:
   - User taps `📥 Download & Upload`.
   - If `TRANSFER_SEMAPHORE` is busy, message updates to `⏳ Transfer Queued (#pos)` showing the active transfer's name and progress.
   - When semaphore acquires, status changes to `⚡ Starting Transfer...`.
5. **Streaming Download**:
   - Provider streams to temporary staging path: `downloads/{uuid}_{safe_filename}`.
   - Real-time progress card updates every 3.5s with speed (MB/s), bar, ETA, and downloaded bytes.
   - Hard limit checked: if file exceeds 2048 MiB or disk space is insufficient, process aborts immediately.
6. **Resumable Google Drive Upload**:
   - File is uploaded to the user's selected Drive folder using 5 MB resumable chunks.
   - Progress card shows `📤 Uploading to Google Drive: XX%`.
7. **Completion & Notifications**:
   - Final completion card updated in message.
   - Explicit Telegram notification sent to chat:
     `🎉 Transfer Complete: <filename> uploaded to <folder>! 🔗 <Drive link>`
   - Temporary file deleted in `finally:` block.

---

## 7. Security and Authorization Design

### Whitelist & Identity Enforcement
- Every command, text message, and inline callback verifies `user.id in config.ALLOWED_USER_IDS`.
- Callback payloads use non-guessable UUID task IDs (`task_id = uuid.uuid4().hex[:8]`) mapped in server-side memory with a 15-minute TTL. Task IDs cannot be forged.

### SSRF (Server-Side Request Forgery) Prevention
- Direct HTTP requests use `validate_url_security()`:
  - Schemes restricted strictly to `http` and `https`.
  - Hostnames resolved via `socket.getaddrinfo()` to verify IP against `ipaddress.ip_address()`.
  - Explicitly blocks:
    - Loopback (`127.0.0.0/8`, `::1`)
    - RFC 1918 Private ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
    - Link-local & Multicast (`169.254.0.0/16`, `224.0.0.0/4`)
    - Cloud Metadata endpoints (`169.254.169.254`, `metadata.google.internal`)
  - `SafeRedirectHandler` validates every redirect target IP before following redirects.

### File & Process Safety
- Downloaded files are treated as untrusted binary payloads.
- Filenames sanitized via `utils.sanitize_filename()` (strips path traversals `../`, invalid characters, shell metacharacters).
- No downloaded file is ever executed or opened with shell interpreters (`os.system`, `subprocess.Popen(shell=True)` are strictly prohibited).
- No sensitive credentials, tokens, or cookies are logged.

### Terms of Service & DRM Compliance
- The bot does not attempt to bypass DRM (Widevine, PlayReady), encrypted video streams, paid paywalls, or private account logins.
- If a video or link requires authentication or DRM, the provider returns a descriptive error explaining that the media cannot be extracted.

---

## 8. Resource and Size-Limit Strategy (Render Free Tier)

| Resource | Free Tier Budget | Allocation & Enforcement |
| :--- | :--- | :--- |
| **RAM** | 512 MB | Zero in-memory buffering. Direct stream chunks are 1 MB for HTTP and piped directly to disk. Pyrogram and Drive client operate at low memory footprints (~60-90 MB total). |
| **Ephemeral Disk** | ~1.5 GB | `MAX_URL_DOWNLOAD_MB = 2048` configured, but guarded dynamically by `utils.check_disk_space()`. If available disk < needed size + 50 MB safety margin, transfer is rejected before downloading. |
| **Concurrency** | 1 vCPU | `MAX_CONCURRENT_URL_DOWNLOADS = 1` enforced via `asyncio.Semaphore(1)`. All additional requests queue gracefully with live status feedback. |
| **Cleanup Guarantee**| N/A | Every transfer wraps file operations in `try ... finally: if path.exists(): path.unlink()`. Additionally, on bot startup, `cleanup_downloads_dir()` removes any orphaned files from prior crashes. |

---

## 9. Testing Strategy

### Unit Tests (`test_url_downloader.py` & `test_providers.py`)
1. **Direct HTTP Provider**:
   - PDF, MP4, MP3, ZIP, generic binary streaming.
   - Missing/zero `Content-Length` (streams until EOF within size limit).
   - Size limit exceeded (> 2048 MB) throws error and cleans up.
   - HTTP 403, 404, connection drop and Range auto-resume.
2. **SSRF Guard**:
   - Block `http://127.0.0.1`, `http://localhost`, `http://10.0.0.1`, `http://169.254.169.254`, `http://metadata.google.internal`.
   - Redirect to private IP triggers `SSRFSecurityError`.
3. **Cloud Share Provider**:
   - Dropbox URL rewriting (`dl=0` -> `dl=1`).
   - Google Drive public share ID extraction.
   - GitHub release URL passthrough.
4. **Media Extractor Provider**:
   - URL recognition: YouTube watch, shorts, youtu.be, Facebook reels.
   - Mocked extraction response returning metadata and audio/video stream URLs.
   - Error handling for private/DRM-protected URLs.
5. **Provider Registry**:
   - Priority matching and fallback to direct HTTP.
   - Unsupported HTML page detection and explanation message.
6. **Queue & Disk Guard**:
   - Pre-flight disk space failure test.
   - Semaphore queue positioning and active transfer card formatting.

---

## 10. Implementation Stages & Verification Checkpoints

```
Stage 1: Repository Audit & Planning [COMPLETED]
  ├── Complete codebase inspection
  ├── Write implementation_plan.md
  └── Approved
        │
Stage 2: Provider Abstraction & Direct HTTP Refactor [COMPLETED]
  ├── Create providers/base.py, providers/registry.py, providers/direct_http.py
  ├── Wire into url_service.py
  └── Verified with unit tests
        │
Stage 3: Cloud Share Providers (Dropbox, Drive, GitHub) [COMPLETED]
  ├── Implement providers/cloud_shares.py
  ├── Register in ProviderRegistry
  └── Verified normalization and link probing
        │
Stage 4: Media Extractor Provider (YouTube, Instagram, Facebook Reels) [COMPLETED]
  ├── Add yt-dlp to requirements.txt
  ├── Implement providers/media_extractor.py
  ├── Handle DRM / login limitations gracefully
  └── Verified video recognition and extraction
        │
Stage 5: UX Enhancement & 2 GB Guarding [COMPLETED]
  ├── Update config.py with MAX_URL_DOWNLOAD_MB, URL_DOWNLOAD_TIMEOUT_SECONDS
  ├── Enhance Telegram interactive cards in handlers.py ([Check Link], [Choose Folder])
  └── Verified queueing, notifications, and size limit enforcement
        │
Stage 6: Antigravity Code Review & Audits [COMPLETED]
  ├── Run /gsd-code-review
  ├── Run /ponytail, /ponytail-review, /ponytail-audit, /ponytail-gain
  └── Verified clean review findings
        │
Stage 7: Documentation & Deployment [COMPLETED]
  ├── Update README.md, .env.example
  ├── Sync to BotgDriveBig workspace
  └── Git commit and push to origin main
```

---

## 11. Risks, Limitations & Rollback Strategy

### Identified Risks & Mitigations
- **Risk 1: Render Ephemeral Disk Saturation**:
  - *Mitigation*: Pre-flight disk space check `utils.check_disk_space()` before downloading. Single-transfer semaphore lock. Immediate file deletion in `finally:`.
- **Risk 2: YouTube / Media Extraction Rate Limits**:
  - *Mitigation*: No concurrent media downloads; pass browser-like headers; cleanly catch `DownloadError` and explain to user rather than looping.
- **Risk 3: Telegram FloodWait on Progress Updates**:
  - *Mitigation*: Throttle update interval to 3.5s in `ProgressTracker` and catch `FloodWait` with `await asyncio.sleep(fw.value)`.

### Rollback Strategy
If any regressions occur during implementation:
1. Revert Git commits via `git revert <commit_hash>` or restore previous branch head.
2. The modular `providers/` architecture allows disabling media extraction or specific providers instantly in `providers/registry.py` without modifying the core bot or Drive upload pipelines.
3. The previous `url_service.py` is safely backed up and can be restored in a single command.

---

## 12. Verification Commands Available

- `/gsd-code-review` — Source code review for bugs, vulnerabilities, and code quality.
- `/ponytail` — Minimal, simplest, standard-library-first implementation rules.
- `/ponytail-review` — Diff review targeting over-engineering and unneeded complexity.
- `/ponytail-audit` — Repo-wide complexity and dependency scan.
- `/ponytail-gain` — Benchmark median impact scoreboard.
