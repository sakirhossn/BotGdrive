"""Google Drive OAuth 2.0 Token Generator.

Run this script locally on your PC (where you have a web browser) to generate a
fresh, long-lived token.json for Google Drive API.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

# Ensure UTF-8 output and line buffering on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

BASE_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"
SCOPES = ["https://www.googleapis.com/auth/drive"]


def main() -> None:
    print("=" * 70, flush=True)
    print("  BotGdrive: Google Drive OAuth Token Generator", flush=True)
    print("=" * 70, flush=True)

    if not CREDENTIALS_FILE.exists():
        print(f"\n[ERROR] '{CREDENTIALS_FILE.name}' was not found at:", flush=True)
        print(f"   {CREDENTIALS_FILE}", flush=True)
        print("\nPlease download your OAuth client credentials JSON from Google Cloud Console", flush=True)
        print(f"and save it as '{CREDENTIALS_FILE.name}' in this directory.", flush=True)
        sys.exit(1)

    # Remove old/expired token if present
    if TOKEN_FILE.exists():
        try:
            TOKEN_FILE.unlink()
            print(f"[INFO] Removed existing '{TOKEN_FILE.name}' to ensure a fresh token.", flush=True)
        except Exception as exc:
            print(f"[WARNING] Could not remove old token file: {exc}", flush=True)

    print("\n[INFO] Starting OAuth flow...", flush=True)
    print("       A browser window will open asking you to authorize Google Drive.", flush=True)
    print("       Sign in with your Google Account and click 'Continue' / 'Allow'.", flush=True)
    print("       (If you see 'Google hasn't verified this app', click 'Advanced' -> 'Go to Bot (unsafe)')\n", flush=True)

    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        print(f"[LINK] If the browser doesn't open automatically, copy & open this URL:\n{auth_url}\n", flush=True)
        creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    except Exception as exc:
        print(f"\n[ERROR] OAuth authorization failed: {exc}", flush=True)
        sys.exit(1)

    # Save to token.json
    token_json_str = creds.to_json()
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token_json_str)

    print(f"\n[SUCCESS] Fresh token generated and saved to '{TOKEN_FILE.name}'!", flush=True)

    # Verify connection
    try:
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        about = drive.about().get(fields="user").execute()
        user_info = about.get("user", {})
        print(f"[AUTH] Authenticated as: {user_info.get('displayName')} ({user_info.get('emailAddress')})", flush=True)
    except Exception as exc:
        print(f"[WARNING] Token saved, but verification call failed: {exc}", flush=True)

    print("\n" + "=" * 70, flush=True)
    print("  RENDER ENVIRONMENT VARIABLE: TOKEN_JSON_CONTENT", flush=True)
    print("=" * 70, flush=True)
    print("1. Go to your Render Dashboard (https://dashboard.render.com).", flush=True)
    print("2. Open your BotGdrive service -> 'Environment'.", flush=True)
    print("3. Edit the variable: TOKEN_JSON_CONTENT", flush=True)
    print("4. Replace its value with the exact content below (copy everything between the lines):", flush=True)
    print("-" * 70, flush=True)
    print(token_json_str, flush=True)
    print("-" * 70, flush=True)
    print("5. Click 'Save Changes'. Render will automatically restart and come online!", flush=True)
    print("=" * 70, flush=True)


if __name__ == "__main__":
    main()
