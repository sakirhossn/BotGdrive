"""Google Drive OAuth 2.0 Token Generator.

Run this script locally on your PC (where you have a web browser) to generate a
fresh, long-lived token.json for Google Drive API.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

BASE_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"
SCOPES = ["https://www.googleapis.com/auth/drive"]


def main() -> None:
    print("=" * 70)
    print("  🔑 BotGdrive: Google Drive OAuth Token Generator")
    print("=" * 70)

    if not CREDENTIALS_FILE.exists():
        print(f"\n❌ Error: '{CREDENTIALS_FILE.name}' was not found at:")
        print(f"   {CREDENTIALS_FILE}")
        print("\nPlease download your OAuth client credentials JSON from Google Cloud Console")
        print(f"and save it as '{CREDENTIALS_FILE.name}' in this directory.")
        sys.exit(1)

    # Remove old/expired token if present
    if TOKEN_FILE.exists():
        try:
            TOKEN_FILE.unlink()
            print(f"🗑️ Removed existing '{TOKEN_FILE.name}' to ensure a fresh token.")
        except Exception as exc:
            print(f"⚠️ Warning: Could not remove old token file: {exc}")

    print("\n🌐 A browser window will now open to authorize Google Drive access.")
    print("   Sign in with your Google Account and click 'Continue' / 'Allow'.")
    print("   (If you see 'Google hasn't verified this app', click 'Advanced' -> 'Go to Bot (unsafe)')\n")

    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    except Exception as exc:
        print(f"\n❌ OAuth authorization failed: {exc}")
        sys.exit(1)

    # Save to token.json
    token_json_str = creds.to_json()
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token_json_str)

    print(f"\n✅ Fresh token successfully generated and saved to '{TOKEN_FILE.name}'!")

    # Verify connection
    try:
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        about = drive.about().get(fields="user").execute()
        user_info = about.get("user", {})
        print(f"👤 Authenticated as: {user_info.get('displayName')} ({user_info.get('emailAddress')})")
    except Exception as exc:
        print(f"⚠️ Warning: Token saved, but verification call failed: {exc}")

    print("\n" + "=" * 70)
    print("  📌 CRITICAL: PREVENT TOKEN FROM EXPIRING EVERY 7 DAYS")
    print("=" * 70)
    print("By default, Google expires OAuth tokens after 7 days if your app is in 'Testing' mode.")
    print("To make this token permanent:")
    print("  1. Go to Google Cloud Console: https://console.cloud.google.com/apis/credentials/consent")
    print("  2. Under 'Publishing status', click 'PUBLISH APP'.")
    print("  3. Confirm the dialog. Publishing status will change to 'In production'.")
    print("  👉 In production mode, your refresh token will NEVER expire after 7 days!")

    print("\n" + "=" * 70)
    print("  🚀 UPDATE RENDER ENVIRONMENT VARIABLE")
    print("=" * 70)
    print("1. Go to your Render Dashboard (https://dashboard.render.com).")
    print("2. Open your BotGdrive service -> 'Environment'.")
    print("3. Edit the variable: TOKEN_JSON_CONTENT")
    print("4. Replace its value with the exact content below (copy everything between the lines):")
    print("-" * 70)
    print(token_json_str)
    print("-" * 70)
    print("5. Click 'Save Changes'. Render will automatically redeploy and start working!")
    print("=" * 70)


if __name__ == "__main__":
    main()
