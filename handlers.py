"""Pyrogram event handlers for 2 GB MTProto media transfers, Drive navigation, and commands."""

from __future__ import annotations

import asyncio
import functools
import html
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Callable, List, Optional

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import drive_service
from state_manager import state_manager
import utils

logger = logging.getLogger(__name__)


def restricted(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to enforce whitelist authorization on Pyrogram handlers."""

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


def build_main_menu(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Generate main dashboard message and inline keyboard."""
    folder_id, folder_name = state_manager.get_upload_folder(user_id)
    text = (
        "⚡ <b>Google Drive 2 GB MTProto Bot</b>\n\n"
        "Send any file, video, or audio up to <b>2,000 MB (2 GB)</b> directly to Google Drive.\n\n"
        f"📁 <b>Current Target Folder:</b> <code>{html.escape(folder_name)}</code>\n\n"
        "Choose an option below:"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📁 Browse Drive", callback_data="nav:root"),
                InlineKeyboardButton("📂 Set Upload Folder", callback_data="nav:select_mode:root"),
            ],
            [
                InlineKeyboardButton("➕ Create Folder", callback_data="act:mkdir"),
                InlineKeyboardButton("🔎 Search Files", callback_data="act:search"),
            ],
            [
                InlineKeyboardButton("📊 System Status", callback_data="act:status"),
                InlineKeyboardButton("📖 Help Guide", callback_data="act:help"),
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

    # If select mode, give button to select this folder as target
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

    # Pagination row
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

    # Action / Parent row
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
    text, keyboard = build_main_menu(message.from_user.id)
    await message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


@restricted
async def help_command(client: Client, message: Message) -> None:
    """Handle /help command."""
    help_text = (
        "📖 <b>Google Drive 2 GB Bot — Command Guide</b>\n\n"
        "<b>Large File Transfer (up to 2,000 MB):</b>\n"
        "• Simply forward or upload any Document, Video, or Audio file.\n"
        "• Live real-time download & upload progress bars with transfer speed and ETA.\n"
        "• Automatic local cleanup immediately after Google Drive confirmation.\n\n"
        "<b>Commands:</b>\n"
        "• /start — Main dashboard menu\n"
        "• /list or /browse — Explore Drive folders & files\n"
        "• /setfolder — Pick destination folder for your uploads\n"
        "• /mkdir &lt;folder_name&gt; — Create a new folder\n"
        "• /search &lt;keyword&gt; — Search files inside Drive\n"
        "• /status — Check bot health, MTProto and Drive status\n"
        "• /cancel — Cancel any active input prompt\n"
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

        # Calculate downloads directory size
        dl_size = sum(f.stat().st_size for f in config.DOWNLOADS_DIR.glob("**/*") if f.is_file())

        text = (
            "📊 <b>System & Service Status</b>\n\n"
            "🟢 <b>MTProto Protocol:</b> Connected (2 GB Support Active)\n"
            f"🟢 <b>Google Drive API:</b> Connected\n"
            f"📁 <b>Drive Root Folder:</b> <code>{html.escape(root_name)}</code>\n"
            f"📌 <b>Active Upload Destination:</b> <code>{html.escape(target_name)}</code>\n"
            f"💾 <b>Local Cache Usage:</b> {utils.format_file_size(dl_size)}\n"
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


# ---------------- CALLBACK QUERY HANDLER ---------------- #

@restricted
async def callback_handler(client: Client, callback: CallbackQuery) -> None:
    """Route interactive inline keyboard callbacks."""
    data = callback.data
    user_id = callback.from_user.id

    if data == "noop":
        await callback.answer()
        return

    if data == "menu:main":
        text, keyboard = build_main_menu(user_id)
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
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
        # Selected a folder as upload destination
        selected_folder_id = data.split(":", 1)[1]
        try:
            meta = await asyncio.to_thread(drive_service.default_drive_service.get_metadata, selected_folder_id)
            folder_name = meta.get("name", "Unknown Folder")
            state_manager.set_upload_folder(user_id, selected_folder_id, folder_name)
            await callback.answer(f"✅ Target folder set to: {folder_name}", show_alert=True)
            text, keyboard = build_main_menu(user_id)
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception as exc:
            await callback.answer(f"❌ Failed to set folder: {exc}", show_alert=True)
        return

    if data.startswith("nav:") or data.startswith("refresh:"):
        # Navigate or refresh folder
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

@restricted
async def media_handler(client: Client, message: Message) -> None:
    """Download large media files via MTProto (up to 2 GB) and upload to Google Drive."""
    user = message.from_user

    # Extract media info
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

    status_msg = await message.reply_text(
        f"⏳ <b>Initiating MTProto Download:</b> <code>{html.escape(safe_name)}</code>\n"
        f"📦 Size: {utils.format_file_size(file_size)}\n"
        f"📁 Target Folder: <b>{html.escape(target_name)}</b>",
        parse_mode=ParseMode.HTML,
    )

    unique_id = uuid.uuid4().hex[:8]
    local_dest = config.DOWNLOADS_DIR / f"{unique_id}_{safe_name}"

    dl_tracker = utils.ProgressTracker(
        action_name="📥 Downloading from Telegram",
        filename=safe_name,
        total_bytes=file_size,
        update_interval=3.5,
    )

    async def dl_progress(current: int, total: int) -> None:
        if dl_tracker.should_update():
            text = dl_tracker.format_status(current)
            try:
                await status_msg.edit_text(text, parse_mode=ParseMode.HTML)
            except MessageNotModified:
                pass
            except FloodWait as fw:
                await asyncio.sleep(fw.value)
            except Exception as e:
                logger.debug("Download progress edit error: %s", e)

    try:
        # Step 1: Download file via native MTProto (handles up to 2,000 MB)
        logger.info("Starting MTProto download for '%s' (size=%s)", safe_name, file_size)
        downloaded_path = await client.download_media(
            message=message,
            file_name=str(local_dest),
            progress=dl_progress,
        )

        if not downloaded_path or not Path(downloaded_path).exists():
            raise FileNotFoundError("Downloaded file was not found on disk.")

        actual_size = Path(downloaded_path).stat().st_size
        logger.info("Downloaded '%s' (%s bytes). Preparing Drive upload...", safe_name, actual_size)

        # Step 2: Upload to Google Drive with 20 MB chunked resumable progress
        await status_msg.edit_text(
            f"🚀 <b>Uploading to Google Drive...</b>\n\n"
            f"📄 <b>File:</b> <code>{html.escape(safe_name)}</code>\n"
            f"📁 <b>Destination:</b> <b>{html.escape(target_name)}</b>",
            parse_mode=ParseMode.HTML,
        )

        loop = asyncio.get_running_loop()
        up_tracker = utils.ProgressTracker(
            action_name="🚀 Uploading to Google Drive",
            filename=safe_name,
            total_bytes=actual_size,
            update_interval=3.5,
        )

        def up_progress(current_bytes: int, total_bytes: int) -> None:
            if up_tracker.should_update():
                text = up_tracker.format_status(current_bytes)

                async def _update() -> None:
                    try:
                        await status_msg.edit_text(text, parse_mode=ParseMode.HTML)
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

        await status_msg.edit_text(
            f"✅ <b>Upload to Google Drive Successful!</b>\n\n"
            f"📄 <b>File:</b> <code>{html.escape(uploaded.get('name', safe_name))}</code>\n"
            f"📦 <b>Size:</b> {utils.format_file_size(actual_size)}\n"
            f"📁 <b>Folder:</b> <b>{html.escape(target_name)}</b>\n\n"
            f"🔗 <a href='{view_link}'>Open in Google Drive</a>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )
        logger.info("Completed full transfer for '%s' to folder '%s'", safe_name, target_name)

    except Exception as exc:
        logger.exception("Media transfer failed for '%s': %s", safe_name, exc)
        await status_msg.edit_text(
            f"❌ <b>Transfer Failed:</b>\n\n<code>{html.escape(str(exc))}</code>",
            parse_mode=ParseMode.HTML,
        )
    finally:
        # Step 3: Cleanup local storage immediately
        try:
            if local_dest.exists():
                local_dest.unlink(missing_ok=True)
                logger.info("Cleaned up temporary file '%s'", local_dest)
        except Exception as cleanup_err:
            logger.warning("Failed to delete temp file %s: %s", local_dest, cleanup_err)
