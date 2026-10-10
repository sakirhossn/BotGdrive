"""Self-test script for BotGdrive."""

import config
import drive_service
from pyrogram import Client


def main() -> None:
    print("=" * 60)
    print("Running BotGdrive Connection & Configuration Self-Test")
    print("=" * 60)

    # 1. Test Config
    errors = config.validate_config()
    if errors:
        print("  -> Configuration errors detected:")
        for e in errors:
            print(f"     - {e}")
        return
    print("  -> SUCCESS! Configuration validated.")
    print(f"  -> Whitelisted user IDs: {config.ALLOWED_USER_IDS}")

    # 2. Test Google Drive
    root = drive_service.default_drive_service.get_root_folder()
    print(f"  -> SUCCESS! Google Drive Root Folder: '{root.get('name')}' (ID: {root.get('id')})")

    # 3. Test Telegram MTProto
    app = Client(
        name="test_session",
        api_id=config.TELEGRAM_API_ID,
        api_hash=config.TELEGRAM_API_HASH,
        bot_token=config.TELEGRAM_BOT_TOKEN,
        workdir=str(config.BASE_DIR),
        in_memory=True,
    )

    async def _test() -> None:
        async with app:
            me = await app.get_me()
            print(f"  -> SUCCESS! Connected to Telegram MTProto as @{me.username} (ID: {me.id})")
            print(f"  -> Bot Name: {me.first_name}")

    app.run(_test())

    print("=" * 60)
    print("ALL TESTS PASSED! Bot is 100% ready for Render & Local execution.")
    print("=" * 60)


if __name__ == "__main__":
    main()
