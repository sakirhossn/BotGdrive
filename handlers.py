"""Pyrogram event handlers for 2 GB MTProto media transfers, Drive navigation, and group sharing."""

from __future__ import annotations

import asyncio
import functools
import html
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, List, Optional

from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus, ChatType, ParseMode
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import drive_service
from state_manager import state_manager
import utils

logger = logging.getLogger(__name__)


async def is_authorized_user(client: Client, message: Message) -> bool:
    """Check if user is authorized in private chat or is an admin/owner in group."""
    user = message.from_user
    if not user:
        return False
    if user.id in config.ALLOWED_USER_IDS:
        return True
    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        try:
            member = await client.get_chat_member(message.chat.id, user.id)
            if member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR):
                return True
        except Exception as exc:
            logger.debug("Failed to check group member status: %s", exc)
            return False
    return False


def restricted(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to enforce whitelist authorization on private chat handlers."""

    @functools.wraps(func)
    async def wrapper(client: Client, update: Message | CallbackQuery, *args: Any, **kwargs: Any) -> Any:
        user = update.from_user
        if not user or user.id not in config.ALLOWED_USER_IDS:
            user_id_str = str(user.id) if user else "Unknown"
            logger.warning("Unauthorized access blocked for user_id=%s", user_id_str)
            if isinstance(update, CallbackQuery):
                await update.answer("❌ Not authorized.", show_alert=True)
            elif isinstance(update, Message):
                await update.reply_text("❌ Not authorized. You are not on the bot's whitelist.")
            return None
        return await func(client, update, *args, **kwargs)

    return wrapper


def build_main_menu(user_id: int, user_first_name: str = "User") -> tuple[str, InlineKeyboardMarkup]:
    """Generate sleek redesigned main dashboard message and inline keyboard."""
    folder_id, folder_name = state_manager.get_upload_folder(user_id)
    text = (
        "☁️ <b>Google Drive MTProto Manager</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👋 Welcome, <b>{html.escape(user_first_name)}</b>!\n"
        "⚡ <b>Transfer Limit:</b> Up to <b>2,000 MB (2 GB)</b>\n"
        f"📁 <b>Target Folder:</b> <code>{html.escape(folder_name)}</code>\n"
        "🟢 <b>Status:</b> 24/7 Cloud Linked\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Tip: Send or forward any video, audio, or document to upload to Drive.</i>\n"
        "<i>In groups, Admins can use <code>/getfile</code> to share files from Drive!</i>"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📁 Browse Drive", callback_data="nav:root"),
                InlineKeyboardButton("📂 Change Folder", callback_data="nav:select_mode:root"),
            ],
            [
                InlineKeyboardButton("➕ Create Folder", callback_data="act:mkdir"),
                InlineKeyboardButton("🔎 Search Files", callback_data="act:search"),
            ],
            [
                InlineKeyboardButton("📤 Group Sharing Info", callback_data="act:group_info"),
                InlineKeyboardButton("📊 System Status", callback_data="act:status"),
            ],
            [
                InlineKeyboardButton("📖 Help & Guide", callback_data="act:help"),
            ],
        ]
    )
    return text, keyboard


def build_folder_browser_keyboard(
    folder_id: str,
    parent_id: Optional[str],
    folders: List[dict[str, Any]],
    files: List[dict[str, Any]],
    page: int = 0,
    is_select_mode: bool = False,
    items_per_page: int = 6,
) -> tuple[str, InlineKeyboardMarkup]:
    """Build an interactive paginated folder browser."""
    total_folders = len(folders)
    total_files = len(files)
    all_items = [(True, f) for f in folders] + [(False, f) for f in files]

    total_pages = max(1, (len(all_items) + items_per_page - 1) // items_per_page)
    page = max(0, min(page, total_pages - 1))
    start_idx = page * items_per_page
    page_items = all_items[start_idx : start_idx + items_per_page]

    buttons: List[List[InlineKeyboardButton]] = []

    if is_select_mode:
        buttons.append(
            [InlineKeyboardButton("✅ Set THIS folder as Upload Destination", callback_data=f"sel:{folder_id}")]
        )

    for is_folder, item in page_items:
        item_name = item.get("name", "Untitled")
        item_id = item.get("id")
        if is_folder:
            label = utils.truncate_button_text(f"📁 {item_name}", 32)
            cb = f"nav:sel_mode:{item_id}" if is_select_mode else f"nav:{item_id}"
            buttons.append([InlineKeyboardButton(label, callback_data=cb)])
        else:
            size_str = utils.format_file_size(int(item.get("size", 0)))
            label = utils.truncate_button_text(f"📄 {item_name} ({size_str})", 32)
            web_link = item.get("webViewLink") or drive_service.default_drive_service.build_drive_link(item_id)
            buttons.append([InlineKeyboardButton(label, url=web_link)])

    nav_row: List[InlineKeyboardButton] = []
    if page > 0:
        prev_cb = f"page:{folder_id}:{page - 1}:{'1' if is_select_mode else '0'}"
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=prev_cb))
    if total_pages > 1:
        nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        next_cb = f"page:{folder_id}:{page + 1}:{'1' if is_select_mode else '0'}"
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=next_cb))

    if nav_row:
        buttons.append(nav_row)

    bottom_row: List[InlineKeyboardButton] = []
    if parent_id and parent_id != folder_id:
        back_cb = f"nav:sel_mode:{parent_id}" if is_select_mode else f"nav:{parent_id}"
        bottom_row.append(InlineKeyboardButton("⬆️ Up", callback_data=back_cb))

    bottom_row.append(InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh:{folder_id}:{'1' if is_select_mode else '0'}"))
    bottom_row.append(InlineKeyboardButton("🏠 Menu", callback_data="menu:main"))
    buttons.append(bottom_row)

    mode_title = "📂 <b>Select Upload Destination Folder:</b>" if is_select_mode else "📁 <b>Drive Browser:</b>"
    text = (
        f"{mode_title}\n\n"
        f"Folders: {total_folders} | Files: {total_files}\n"
        f"Page {page + 1} of {total_pages}"
    )
    return text, InlineKeyboardMarkup(buttons)


# ---------------- COMMAND HANDLERS ---------------- #

@restricted
async def start_command(client: Client, message: Message) -> None:
    """Handle /start command."""
    first_name = message.from_user.first_name if message.from_user else "User"
    text, keyboard = build_main_menu(message.from_user.id, first_name)
    await message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


@restricted
async def help_command(client: Client, message: Message) -> None:
    """Handle /help command."""
    help_text = (
        "📖 <b>Google Drive 2 GB Bot — Command Guide</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Direct Large File Transfers (up to 2,000 MB):</b>\n"
        "• Forward or upload any Document, Video, or Audio.\n"
        "• Real-time progress updates with speed and ETA.\n"
        "• Auto cleanup immediately after Drive confirmation.\n\n"
        "<b>Private Chat Commands:</b>\n"
        "• /start — Open main dashboard menu\n"
        "• /list or /browse — Explore Drive folders & files\n"
        "• /setfolder — Pick destination folder for your uploads\n"
        "• /mkdir &lt;folder_name&gt; — Create a new folder\n"
        "• /search &lt;keyword&gt; — Search files inside Drive\n"
        "• /status — Check bot health, MTProto and Drive status\n"
        "• /cancel — Cancel any active interactive prompt\n\n"
        "<b>Group Commands (for Admins / Owner):</b>\n"
        "• /getfile &lt;keyword&gt; — Fetch file from Drive and post directly into the group\n"
        "• /link &lt;keyword&gt; — Post direct Google Drive link into group\n"
        "• Reply with /upload or /save — Save any group file into Drive\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    await message.reply_text(help_text, parse_mode=ParseMode.HTML)


@restricted
async def status_command(client: Client, message: Message) -> None:
    """Handle /status command."""
    status_msg = await message.reply_text("🔍 Checking system status...")
    try:
        root_meta = await asyncio.to_thread(drive_service.default_drive_service.get_root_folder)
        root_name = root_meta.get("name", "Unknown")
        target_id, target_name = state_manager.get_upload_folder(message.from_user.id)

        dl_size = sum(f.stat().st_size for f in config.DOWNLOADS_DIR.glob("**/*") if f.is_file())

        text = (
            "📊 <b>System & Service Status</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🟢 <b>MTProto Protocol:</b> Connected (2 GB Support Active)\n"
            f"🟢 <b>Google Drive API:</b> Connected\n"
            f"📁 <b>Drive Root Folder:</b> <code>{html.escape(root_name)}</code>\n"
            f"📌 <b>Active Upload Destination:</b> <code>{html.escape(target_name)}</code>\n"
            f"💾 <b>Local Cache Usage:</b> {utils.format_file_size(dl_size)}\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        await status_msg.edit_text(text, parse_mode=ParseMode.HTML)
    except Exception as exc:
        logger.exception("Status check failed: %s", exc)
        await status_msg.edit_text(f"❌ <b>Error checking status:</b> {html.escape(str(exc))}", parse_mode=ParseMode.HTML)


@restricted
async def list_command(client: Client, message: Message) -> None:
    """Handle /list and /browse commands."""
    wait_msg = await message.reply_text("📂 Loading folder contents...")
    target_id, _ = state_manager.get_upload_folder(message.from_user.id)
    try:
        folders, files = await asyncio.to_thread(drive_service.default_drive_service.list_children, target_id)
        text, markup = build_folder_browser_keyboard(
            folder_id=target_id,
            parent_id=None,
            folders=folders,
            files=files,
            page=0,
            is_select_mode=False,
        )
        await wait_msg.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    except Exception as exc:
        logger.exception("Failed to list folder: %s", exc)
        await wait_msg.edit_text(f"❌ <b>Error loading folder:</b> {html.escape(str(exc))}", parse_mode=ParseMode.HTML)


@restricted
async def setfolder_command(client: Client, message: Message) -> None:
    """Handle /setfolder command."""
    wait_msg = await message.reply_text("📂 Opening folder selection...")
    root_id = config.DRIVE_FOLDER_ID
    try:
        folders, files = await asyncio.to_thread(drive_service.default_drive_service.list_children, root_id)
        text, markup = build_folder_browser_keyboard(
            folder_id=root_id,
            parent_id=None,
            folders=folders,
            files=files,
            page=0,
            is_select_mode=True,
        )
        await wait_msg.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    except Exception as exc:
        logger.exception("Failed to open folder selection: %s", exc)
        await wait_msg.edit_text(f"❌ <b>Error:</b> {html.escape(str(exc))}", parse_mode=ParseMode.HTML)


@restricted
async def mkdir_command(client: Client, message: Message) -> None:
    """Handle /mkdir command."""
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip():
        folder_name = parts[1].strip()
        target_id, target_name = state_manager.get_upload_folder(message.from_user.id)
        wait_msg = await message.reply_text(f"⏳ Creating folder '<b>{html.escape(folder_name)}</b>' in {html.escape(target_name)}...")
        try:
            created = await asyncio.to_thread(drive_service.default_drive_service.create_folder, folder_name, target_id)
            link = created.get("webViewLink") or drive_service.default_drive_service.build_drive_link(created["id"])
            await wait_msg.edit_text(
                f"✅ Folder <b>{html.escape(folder_name)}</b> created successfully!\n\n"
                f"🔗 <a href='{link}'>View in Google Drive</a>",
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            logger.exception("Failed to create folder: %s", exc)
            await wait_msg.edit_text(f"❌ <b>Failed to create folder:</b> {html.escape(str(exc))}", parse_mode=ParseMode.HTML)
    else:
        state_manager.set_user_action(message.from_user.id, "awaiting_folder_name")
        await message.reply_text(
            "📝 Please send the name for the new folder, or /cancel to abort:",
            parse_mode=ParseMode.HTML,
        )


@restricted
async def search_command(client: Client, message: Message) -> None:
    """Handle /search command."""
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        state_manager.set_user_action(message.from_user.id, "awaiting_search_query")
        await message.reply_text("🔎 Please type the filename or keyword to search for, or /cancel to abort:")
        return

    query = parts[1].strip()
    wait_msg = await message.reply_text(f"🔍 Searching for '<b>{html.escape(query)}</b>'...", parse_mode=ParseMode.HTML)
    try:
        matched = await asyncio.to_thread(drive_service.default_drive_service.search_files, query)
        if not matched:
            await wait_msg.edit_text(f"🔍 No files found matching '<b>{html.escape(query)}</b>'.", parse_mode=ParseMode.HTML)
            return

        lines = [f"🔍 <b>Found {len(matched)} file(s) matching '{html.escape(query)}':</b>\n"]
        for f in matched[:10]:
            size_str = utils.format_file_size(int(f.get("size", 0)))
            link = f.get("webViewLink") or drive_service.default_drive_service.build_drive_link(f["id"])
            lines.append(f"• <a href='{link}'><b>{html.escape(f['name'])}</b></a> ({size_str})")

        if len(matched) > 10:
            lines.append(f"\n<i>...and {len(matched) - 10} more files</i>")

        await wait_msg.edit_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as exc:
        logger.exception("Search failed: %s", exc)
        await wait_msg.edit_text(f"❌ <b>Search error:</b> {html.escape(str(exc))}", parse_mode=ParseMode.HTML)


@restricted
async def cancel_command(client: Client, message: Message) -> None:
    """Handle /cancel command."""
    state_manager.clear_user_action(message.from_user.id)
    await message.reply_text("✅ Action cancelled.")


# ---------------- GROUP SHARING COMMANDS ---------------- #

async def getfile_command(client: Client, message: Message) -> None:
    """Handle /getfile command in groups or private chat."""
    if not await is_authorized_user(client, message):
        await message.reply_text("❌ Only group Admins or authorized users can retrieve files from Drive.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply_text(
            "💡 <b>Usage:</b> <code>/getfile &lt;filename or keyword&gt;</code>\n"
            "Example: <code>/getfile Constitution</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    query = parts[1].strip()
    status_msg = await message.reply_text(f"🔍 Searching Drive for '<b>{html.escape(query)}</b>'...", parse_mode=ParseMode.HTML)

    try:
        matched = await asyncio.to_thread(drive_service.default_drive_service.search_files, query)
        if not matched:
            await status_msg.edit_text(f"❌ No files found matching '<b>{html.escape(query)}</b>'.", parse_mode=ParseMode.HTML)
            return

        if len(matched) == 1:
            # Single match: deliver directly
            target_file = matched[0]
            await _deliver_file_to_chat(client, message.chat.id, status_msg, target_file)
        else:
            # Multiple matches: show choice buttons to admin
            buttons = []
            for f in matched[:6]:
                f_size = utils.format_file_size(int(f.get("size", 0)))
                label = utils.truncate_button_text(f"📄 {f['name']} ({f_size})", 32)
                buttons.append([InlineKeyboardButton(label, callback_data=f"grp_send:{f['id']}")])
            buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="grp_cancel")])

            await status_msg.edit_text(
                f"🔎 <b>Found {len(matched)} matching files.</b>\nSelect the file to post into this chat:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode=ParseMode.HTML,
            )

    except Exception as exc:
        logger.exception("Failed to search files for getfile: %s", exc)
        await status_msg.edit_text(f"❌ Error searching Drive: {html.escape(str(exc))}", parse_mode=ParseMode.HTML)


async def link_command(client: Client, message: Message) -> None:
    """Handle /link command to share direct Google Drive link."""
    if not await is_authorized_user(client, message):
        await message.reply_text("❌ Only group Admins or authorized users can request file links.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply_text("💡 Usage: <code>/link &lt;filename or keyword&gt;</code>", parse_mode=ParseMode.HTML)
        return

    query = parts[1].strip()
    try:
        matched = await asyncio.to_thread(drive_service.default_drive_service.search_files, query)
        if not matched:
            await message.reply_text(f"❌ No files found matching '<b>{html.escape(query)}</b>'.", parse_mode=ParseMode.HTML)
            return

        lines = [f"🔗 <b>Google Drive Results for '{html.escape(query)}':</b>\n"]
        for f in matched[:5]:
            size_str = utils.format_file_size(int(f.get("size", 0)))
            link = f.get("webViewLink") or drive_service.default_drive_service.build_drive_link(f["id"])
            lines.append(f"• <a href='{link}'><b>{html.escape(f['name'])}</b></a> <i>({size_str})</i>")

        await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=False)
    except Exception as exc:
        await message.reply_text(f"❌ Error: {html.escape(str(exc))}", parse_mode=ParseMode.HTML)


async def upload_reply_command(client: Client, message: Message) -> None:
    """Handle /upload or /save in response to a media message in groups."""
    if not await is_authorized_user(client, message):
        await message.reply_text("❌ Only group Admins or authorized users can save files to Drive.")
        return

    if not message.reply_to_message:
        await message.reply_text("💡 Reply to any media message (document, video, audio) with <code>/upload</code> to save it to Drive.")
        return

    await media_handler(client, message.reply_to_message)


async def _deliver_file_to_chat(client: Client, chat_id: int, status_msg: Message, file_meta: dict[str, Any]) -> None:
    """Download file from Google Drive and upload to Telegram chat (up to 2 GB)."""
    file_id = file_meta["id"]
    file_name = file_meta.get("name", "downloaded_file")
    file_size = int(file_meta.get("size", 0))
    mime_type = file_meta.get("mimeType", "")

    unique_id = uuid.uuid4().hex[:8]
    safe_name = utils.sanitize_filename(file_name)
    local_dest = config.DOWNLOADS_DIR / f"{unique_id}_{safe_name}"

    dl_tracker = utils.ProgressTracker(
        action_name="📥 Downloading from Drive",
        filename=safe_name,
        total_bytes=file_size,
        update_interval=3.5,
    )

    loop = asyncio.get_running_loop()

    def dl_progress(current: int, total: int) -> None:
        if dl_tracker.should_update():
            card = dl_tracker.format_status(current)

            async def _update() -> None:
                try:
                    await status_msg.edit_text(card, parse_mode=ParseMode.HTML)
                except MessageNotModified:
                    pass
                except Exception:
                    pass

            asyncio.run_coroutine_threadsafe(_update(), loop)

    try:
        await status_msg.edit_text(
            f"⏳ <b>Downloading from Google Drive:</b> <code>{html.escape(safe_name)}</code>\n"
            f"📦 Size: {utils.format_file_size(file_size)}",
            parse_mode=ParseMode.HTML,
        )

        downloaded_path = await asyncio.to_thread(
            drive_service.default_drive_service.download_file,
            file_id=file_id,
            destination_path=local_dest,
            progress_callback=dl_progress,
        )

        # Upload to Telegram chat
        await status_msg.edit_text(
            f"🚀 <b>Posting to Telegram:</b> <code>{html.escape(safe_name)}</code>\n"
            f"📦 Size: {utils.format_file_size(file_size)}",
            parse_mode=ParseMode.HTML,
        )

        caption = f"📄 <b>{html.escape(safe_name)}</b>\n📦 {utils.format_file_size(file_size)}"

        if "video" in mime_type:
            await client.send_video(chat_id, video=str(downloaded_path), caption=caption, parse_mode=ParseMode.HTML)
        elif "audio" in mime_type:
            await client.send_audio(chat_id, audio=str(downloaded_path), caption=caption, parse_mode=ParseMode.HTML)
        else:
            await client.send_document(chat_id, document=str(downloaded_path), caption=caption, parse_mode=ParseMode.HTML)

        await status_msg.edit_text(f"✅ <b>File Delivered:</b> <code>{html.escape(safe_name)}</code>", parse_mode=ParseMode.HTML)

    except Exception as exc:
        logger.exception("Failed to deliver file %s: %s", file_name, exc)
        await status_msg.edit_text(f"❌ Failed to deliver file: {html.escape(str(exc))}", parse_mode=ParseMode.HTML)
    finally:
        if local_dest.exists():
            local_dest.unlink(missing_ok=True)


# ---------------- CALLBACK QUERY HANDLER ---------------- #

async def callback_handler(client: Client, callback: CallbackQuery) -> None:
    """Route interactive inline keyboard callbacks."""
    data = callback.data
    user_id = callback.from_user.id

    if data == "noop":
        await callback.answer()
        return

    if data == "grp_cancel":
        await callback.message.delete()
        await callback.answer("Cancelled.")
        return

    if data.startswith("grp_send:"):
        # Admin selected file to send to group
        file_id = data.split(":", 1)[1]
        await callback.answer("Fetching file from Drive...")
        try:
            meta = await asyncio.to_thread(drive_service.default_drive_service.get_metadata, file_id)
            await _deliver_file_to_chat(client, callback.message.chat.id, callback.message, meta)
        except Exception as exc:
            await callback.message.edit_text(f"❌ Failed: {exc}")
        return

    # Private menu callbacks require whitelist
    if user_id not in config.ALLOWED_USER_IDS:
        await callback.answer("❌ Not authorized.", show_alert=True)
        return

    if data == "menu:main":
        text, keyboard = build_main_menu(user_id, callback.from_user.first_name)
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        await callback.answer()
        return

    if data == "act:group_info":
        info_text = (
            "👥 <b>Group Sharing Guide</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "You can add this bot to your private groups!\n\n"
            "<b>Available Group Features:</b>\n"
            "• <code>/getfile &lt;name&gt;</code> — Downloads file from Drive and posts it into the group (up to 2 GB).\n"
            "• <code>/link &lt;name&gt;</code> — Posts direct Google Drive view link into group.\n"
            "• Reply to any media with <code>/upload</code> — Saves file to Drive.\n\n"
            "🔒 <i>Only Group Admins or Owner can use these commands.</i>\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        await callback.message.edit_text(
            info_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu:main")]]),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
        return

    if data == "act:status":
        await callback.answer("Checking status...")
        await status_command(client, callback.message)
        return

    if data == "act:help":
        await callback.answer()
        await help_command(client, callback.message)
        return

    if data == "act:mkdir":
        state_manager.set_user_action(user_id, "awaiting_folder_name")
        await callback.message.reply_text("📝 Please send the name for the new folder, or /cancel to abort:")
        await callback.answer()
        return

    if data == "act:search":
        state_manager.set_user_action(user_id, "awaiting_search_query")
        await callback.message.reply_text("🔎 Please type the filename or keyword to search for, or /cancel to abort:")
        await callback.answer()
        return

    if data.startswith("sel:"):
        selected_folder_id = data.split(":", 1)[1]
        try:
            meta = await asyncio.to_thread(drive_service.default_drive_service.get_metadata, selected_folder_id)
            folder_name = meta.get("name", "Unknown Folder")
            state_manager.set_upload_folder(user_id, selected_folder_id, folder_name)
            await callback.answer(f"✅ Target folder set to: {folder_name}", show_alert=True)
            text, keyboard = build_main_menu(user_id, callback.from_user.first_name)
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception as exc:
            await callback.answer(f"❌ Failed to set folder: {exc}", show_alert=True)
        return

    if data.startswith("nav:") or data.startswith("refresh:"):
        parts = data.split(":")
        is_select_mode = False
        target_folder_id = config.DRIVE_FOLDER_ID

        if parts[0] == "refresh":
            target_folder_id = parts[1]
            is_select_mode = len(parts) > 2 and parts[2] == "1"
        elif parts[1] == "root":
            target_folder_id = config.DRIVE_FOLDER_ID
        elif parts[1] == "select_mode":
            target_folder_id = parts[2] if len(parts) > 2 and parts[2] != "root" else config.DRIVE_FOLDER_ID
            is_select_mode = True
        elif parts[1] == "sel_mode":
            target_folder_id = parts[2]
            is_select_mode = True
        else:
            target_folder_id = parts[1]

        await callback.answer("Loading...")
        try:
            folders, files = await asyncio.to_thread(drive_service.default_drive_service.list_children, target_folder_id)
            meta = await asyncio.to_thread(drive_service.default_drive_service.get_metadata, target_folder_id)
            parent_id = meta.get("parents", [None])[0] if target_folder_id != config.DRIVE_FOLDER_ID else None

            text, markup = build_folder_browser_keyboard(
                folder_id=target_folder_id,
                parent_id=parent_id,
                folders=folders,
                files=files,
                page=0,
                is_select_mode=is_select_mode,
            )
            await callback.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        except Exception as exc:
            logger.exception("Error navigating folder %s: %s", target_folder_id, exc)
            await callback.answer(f"❌ Error: {exc}", show_alert=True)
        return

    if data.startswith("page:"):
        _, folder_id, page_str, sel_str = data.split(":")
        page = int(page_str)
        is_select_mode = sel_str == "1"
        try:
            folders, files = await asyncio.to_thread(drive_service.default_drive_service.list_children, folder_id)
            meta = await asyncio.to_thread(drive_service.default_drive_service.get_metadata, folder_id)
            parent_id = meta.get("parents", [None])[0] if folder_id != config.DRIVE_FOLDER_ID else None

            text, markup = build_folder_browser_keyboard(
                folder_id=folder_id,
                parent_id=parent_id,
                folders=folders,
                files=files,
                page=page,
                is_select_mode=is_select_mode,
            )
            await callback.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
            await callback.answer()
        except Exception as exc:
            await callback.answer(f"❌ Error: {exc}", show_alert=True)
        return

    await callback.answer()


# ---------------- TEXT PROMPT HANDLER ---------------- #

@restricted
async def text_handler(client: Client, message: Message) -> None:
    """Handle conversational text input (e.g. folder creation name, search query)."""
    user_action = state_manager.get_user_action(message.from_user.id)
    if not user_action:
        return

    action = user_action.get("action")

    if action == "awaiting_folder_name":
        folder_name = message.text.strip()
        state_manager.clear_user_action(message.from_user.id)
        if not folder_name:
            await message.reply_text("❌ Folder name cannot be empty.")
            return

        target_id, target_name = state_manager.get_upload_folder(message.from_user.id)
        wait_msg = await message.reply_text(f"⏳ Creating folder '<b>{html.escape(folder_name)}</b>' in {html.escape(target_name)}...")
        try:
            created = await asyncio.to_thread(drive_service.default_drive_service.create_folder, folder_name, target_id)
            link = created.get("webViewLink") or drive_service.default_drive_service.build_drive_link(created["id"])
            await wait_msg.edit_text(
                f"✅ Folder <b>{html.escape(folder_name)}</b> created successfully!\n\n"
                f"🔗 <a href='{link}'>View in Google Drive</a>",
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            logger.exception("Failed to create folder: %s", exc)
            await wait_msg.edit_text(f"❌ <b>Failed to create folder:</b> {html.escape(str(exc))}", parse_mode=ParseMode.HTML)

    elif action == "awaiting_search_query":
        query = message.text.strip()
        state_manager.clear_user_action(message.from_user.id)
        message.text = f"/search {query}"
        await search_command(client, message)


# ---------------- 2 GB MEDIA UPLOAD PIPELINE ---------------- #

async def media_handler(client: Client, message: Message) -> None:
    """Download large media files via MTProto (up to 2 GB) and upload to Google Drive."""
    user = message.from_user
    if not user or user.id not in config.ALLOWED_USER_IDS:
        logger.warning("Unauthorized media upload attempt by user_id=%s", getattr(user, "id", None))
        return

    filename = None
    file_size = 0
    mime_type = None

    if message.document:
        doc = message.document
        filename = doc.file_name or f"doc_{message.id}"
        file_size = doc.file_size or 0
        mime_type = doc.mime_type
    elif message.video:
        vid = message.video
        filename = vid.file_name or f"video_{message.id}.mp4"
        file_size = vid.file_size or 0
        mime_type = vid.mime_type or "video/mp4"
    elif message.audio:
        aud = message.audio
        filename = aud.file_name or f"audio_{message.id}.mp3"
        file_size = aud.file_size or 0
        mime_type = aud.mime_type or "audio/mpeg"
    elif message.photo:
        photo = message.photo
        filename = f"photo_{message.id}.jpg"
        file_size = photo.file_size or 0
        mime_type = "image/jpeg"
    else:
        return

    safe_name = utils.sanitize_filename(filename)
    target_id, target_name = state_manager.get_upload_folder(user.id)
    start_time = time.time()

    status_msg = await message.reply_text(
        "⚡ <b>Initiating Transfer...</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📄 <b>File:</b> <code>{html.escape(safe_name)}</code>\n"
        f"📦 <b>Size:</b> {utils.format_file_size(file_size)}\n"
        f"📁 <b>Target:</b> <code>{html.escape(target_name)}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.HTML,
    )

    unique_id = uuid.uuid4().hex[:8]
    local_dest = config.DOWNLOADS_DIR / f"{unique_id}_{safe_name}"

    dl_tracker = utils.ProgressTracker(
        action_name="📥 Downloading from Telegram",
        filename=safe_name,
        total_bytes=file_size,
        target_folder=target_name,
        update_interval=3.5,
    )

    async def dl_progress(current: int, total: int) -> None:
        if dl_tracker.should_update():
            card = dl_tracker.format_status(current)
            try:
                await status_msg.edit_text(card, parse_mode=ParseMode.HTML)
            except MessageNotModified:
                pass
            except FloodWait as fw:
                await asyncio.sleep(fw.value)
            except Exception as e:
                logger.debug("Download progress edit error: %s", e)

    try:
        # Step 1: Download via MTProto
        logger.info("Starting MTProto download for '%s' (size=%s)", safe_name, file_size)
        downloaded_path = await client.download_media(
            message=message,
            file_name=str(local_dest),
            progress=dl_progress,
        )

        if not downloaded_path or not Path(downloaded_path).exists():
            raise FileNotFoundError("Downloaded file was not found on disk.")

        actual_size = Path(downloaded_path).stat().st_size
        logger.info("Downloaded '%s' (%s bytes). Uploading to Drive...", safe_name, actual_size)

        # Step 2: Upload to Google Drive
        loop = asyncio.get_running_loop()
        up_tracker = utils.ProgressTracker(
            action_name="☁️ Uploading to Google Drive",
            filename=safe_name,
            total_bytes=actual_size,
            target_folder=target_name,
            update_interval=3.5,
        )

        def up_progress(current_bytes: int, total_bytes: int) -> None:
            if up_tracker.should_update():
                card = up_tracker.format_status(current_bytes)

                async def _update() -> None:
                    try:
                        await status_msg.edit_text(card, parse_mode=ParseMode.HTML)
                    except MessageNotModified:
                        pass
                    except Exception as e:
                        logger.debug("Upload progress edit error: %s", e)

                asyncio.run_coroutine_threadsafe(_update(), loop)

        uploaded = await asyncio.to_thread(
            drive_service.default_drive_service.upload_file,
            local_path=downloaded_path,
            filename=safe_name,
            mime_type=mime_type,
            folder_id=target_id,
            progress_callback=up_progress,
        )

        view_link = uploaded.get("webViewLink") or drive_service.default_drive_service.build_drive_link(uploaded["id"])
        elapsed = time.time() - start_time

        completion_card = utils.format_completion_card(
            filename=uploaded.get("name", safe_name),
            file_size=actual_size,
            folder_name=target_name,
            elapsed_seconds=elapsed,
            view_link=view_link,
        )

        await status_msg.edit_text(completion_card, parse_mode=ParseMode.HTML, disable_web_page_preview=False)
        logger.info("Completed full transfer for '%s' in %.1fs", safe_name, elapsed)

    except Exception as exc:
        logger.exception("Media transfer failed for '%s': %s", safe_name, exc)
        await status_msg.edit_text(
            f"❌ <b>Transfer Failed:</b>\n\n<code>{html.escape(str(exc))}</code>",
            parse_mode=ParseMode.HTML,
        )
    finally:
        if local_dest.exists():
            local_dest.unlink(missing_ok=True)
            logger.info("Cleaned up temporary file '%s'", local_dest)
