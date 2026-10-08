"""Telegram event handlers for media uploads, folder creation, and user interactions.

Implements the upload-folder selection workflow, remembered upload folder,
size validation, temporary file cleanup, and asynchronous Google Drive uploads.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
import drive_service
import utils
from bot import restricted

logger = logging.getLogger(__name__)

# Telegram Bot API limits
MAX_TELEGRAM_DOWNLOAD_SIZE = 20 * 1024 * 1024  # 20 MB


def build_upload_folder_keyboard(
    folders: list[dict[str, Any]],
    current_folder_id: str,
    current_folder_name: str,
) -> InlineKeyboardMarkup:
    """Construct inline keyboard for choosing upload destination folder."""
    buttons: list[list[InlineKeyboardButton]] = []

    # Option to upload directly to current folder
    clean_curr_name = utils.truncate_button_text(f"📁 Current: {current_folder_name}", 32)
    buttons.append([InlineKeyboardButton(clean_curr_name, callback_data=f"up:sel:{current_folder_id}")])

    # Child folders
    for f in folders[:10]:
        name = utils.truncate_button_text(f"📂 {f['name']}", 30)
        # Clicking child folder sets it as target
        buttons.append([
            InlineKeyboardButton(name, callback_data=f"up:sel:{f['id']}"),
            InlineKeyboardButton("➡️ Open", callback_data=f"up:nav:{f['id']}"),
        ])

    # Navigation and Action buttons
    action_row = [
        InlineKeyboardButton("➕ Create New Folder", callback_data=f"up:mkdir:{current_folder_id}"),
        InlineKeyboardButton("❌ Cancel", callback_data="up:cancel"),
    ]
    buttons.append(action_row)

    return InlineKeyboardMarkup(buttons)


@restricted
async def handle_media_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Intercept media messages, validate file size, and prompt for destination folder."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    # Extract media metadata
    file_id: Optional[str] = None
    file_size: Optional[int] = None
    filename: Optional[str] = None
    mime_type: Optional[str] = None

    if message.document:
        doc = message.document
        file_id = doc.file_id
        file_size = doc.file_size
        filename = utils.sanitize_filename(doc.file_name, default_prefix="doc")
        mime_type = doc.mime_type
    elif message.photo:
        # Highest resolution photo is last in list
        photo = message.photo[-1]
        file_id = photo.file_id
        file_size = photo.file_size
        mime_type = "image/jpeg"
        filename = utils.generate_media_filename("photo", mime_type)
    elif message.video:
        vid = message.video
        file_id = vid.file_id
        file_size = vid.file_size
        mime_type = vid.mime_type or "video/mp4"
        filename = utils.sanitize_filename(vid.file_name, default_prefix="video") if vid.file_name else utils.generate_media_filename("video", mime_type)
    elif message.audio:
        aud = message.audio
        file_id = aud.file_id
        file_size = aud.file_size
        mime_type = aud.mime_type or "audio/mpeg"
        filename = utils.sanitize_filename(aud.file_name, default_prefix="audio") if aud.file_name else utils.generate_media_filename("audio", mime_type)
    elif message.voice:
        voi = message.voice
        file_id = voi.file_id
        file_size = voi.file_size
        mime_type = voi.mime_type or "audio/ogg"
        filename = utils.generate_media_filename("voice", mime_type)

    if not file_id:
        return

    # Check Telegram's 20 MB download limit
    if file_size and file_size > MAX_TELEGRAM_DOWNLOAD_SIZE:
        formatted_size = utils.format_file_size(file_size)
        await message.reply_text(
            f"❌ <b>File too large</b>\n\n"
            f"Telegram bots can download files only up to 20 MB using this API configuration.\n\n"
            f"<b>File:</b> {html.escape(filename or 'Unknown')}\n"
            f"<b>Size:</b> {formatted_size}",
            parse_mode=ParseMode.HTML,
        )
        return

    # Store pending upload in user session
    context.user_data["pending_upload"] = {
        "file_id": file_id,
        "filename": filename,
        "file_size": file_size,
        "mime_type": mime_type,
    }

    # Check remembered upload folder
    pref_id, pref_name = utils.get_user_upload_pref(user.id)
    if pref_id and pref_name:
        keyboard = [
            [InlineKeyboardButton("✅ Upload Here", callback_data=f"up:sel:{pref_id}")],
            [InlineKeyboardButton("📂 Choose Another Folder", callback_data="up:choose_other")],
            [InlineKeyboardButton("❌ Cancel", callback_data="up:cancel")],
        ]
        await message.reply_text(
            f"📤 <b>File received</b>\n\n"
            f"📄 <b>{html.escape(filename or 'file')}</b>\n"
            f"📦 {utils.format_file_size(file_size)}\n\n"
            f"Upload to: 📁 <b>{html.escape(pref_name)}</b>?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )
        return

    # Otherwise, show folder selection starting at root folder
    await show_folder_selection(update, context, folder_id=config.DRIVE_FOLDER_ID)


async def show_folder_selection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    folder_id: str,
) -> None:
    """Display interactive folder selection for pending upload."""
    pending = context.user_data.get("pending_upload")
    if not pending:
        if update.callback_query:
            await update.callback_query.answer("No pending upload found.", show_alert=True)
        return

    # Fetch folder details and subfolders in background thread
    try:
        current_meta = await asyncio.to_thread(
            drive_service.default_drive_service.get_metadata, folder_id
        )
        subfolders = await asyncio.to_thread(
            drive_service.default_drive_service.list_folders, folder_id
        )
    except Exception as exc:
        logger.error("Error listing folders for selection (folder_id=%s): %s", folder_id, exc)
        if update.callback_query:
            await update.callback_query.answer("Error fetching folders.", show_alert=True)
        return

    current_name = current_meta.get("name", "Root Folder")
    reply_markup = build_upload_folder_keyboard(subfolders, folder_id, current_name)

    text = (
        f"📤 <b>File received</b>\n\n"
        f"📄 <b>{html.escape(pending['filename'])}</b>\n"
        f"📦 {utils.format_file_size(pending['file_size'])}\n\n"
        f"Where should I upload it?"
    )

    if update.callback_query and update.callback_query.message:
        await update.callback_query.message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
        )
    elif update.effective_message:
        await update.effective_message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
        )


@restricted
async def upload_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries related to upload folder selection."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return

    data = query.data or ""
    await query.answer()

    if data == "up:cancel":
        context.user_data.pop("pending_upload", None)
        await query.message.edit_text("❌ Upload cancelled.")
        return

    if data == "up:choose_other":
        await show_folder_selection(update, context, folder_id=config.DRIVE_FOLDER_ID)
        return

    if data.startswith("up:nav:"):
        target_folder = data[len("up:nav:") :]
        await show_folder_selection(update, context, folder_id=target_folder)
        return

    if data.startswith("up:mkdir:"):
        parent_folder_id = data[len("up:mkdir:") :]
        context.user_data["awaiting_folder_name"] = {
            "parent_id": parent_folder_id,
            "for_upload": True,
        }
        await query.message.reply_text(
            "📁 <b>Enter the new folder name:</b>\n\n"
            "Reply with the name of the folder you want to create.",
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith("up:sel:"):
        target_folder_id = data[len("up:sel:") :]
        await execute_upload(update, context, target_folder_id)


async def execute_upload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    target_folder_id: str,
) -> None:
    """Download pending file from Telegram and upload to Google Drive."""
    query = update.callback_query
    message = query.message if query else update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    pending = context.user_data.pop("pending_upload", None)
    if not pending:
        await message.reply_text("❌ No pending upload found. Please send the file again.")
        return

    file_id = pending["file_id"]
    filename = pending["filename"]
    file_size = pending["file_size"]
    mime_type = pending["mime_type"]

    # Show initial progress message
    status_msg = await message.reply_text("⏳ <b>Uploading...</b>", parse_mode=ParseMode.HTML)

    # Local temporary staging file
    temp_filename = f"temp_{uuid.uuid4().hex}_{filename}"
    local_temp_path = config.DOWNLOADS_DIR / temp_filename

    try:
        # Download from Telegram
        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(custom_path=str(local_temp_path))

        # Fetch target folder metadata
        folder_meta = await asyncio.to_thread(
            drive_service.default_drive_service.get_metadata, target_folder_id
        )
        folder_name = folder_meta.get("name", "Drive Folder")

        # Remember this folder for future uploads
        utils.set_user_upload_pref(user.id, target_folder_id, folder_name)

        # Upload to Google Drive (blocking call executed in thread)
        drive_file = await asyncio.to_thread(
            drive_service.default_drive_service.upload_file,
            local_path=local_temp_path,
            filename=filename,
            mime_type=mime_type,
            folder_id=target_folder_id,
        )

        web_link = drive_file.get("webViewLink") or drive_service.default_drive_service.build_drive_link(drive_file["id"])
        formatted_size = utils.format_file_size(file_size or drive_file.get("size"))

        success_text = (
            f"✅ <b>Uploaded successfully</b>\n\n"
            f"📄 <b>{html.escape(filename)}</b>\n"
            f"📁 {html.escape(folder_name)}\n"
            f"📦 {formatted_size}\n\n"
            f'🔗 <a href="{web_link}">Open in Drive</a>'
        )

        await status_msg.edit_text(
            success_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )

    except Exception as exc:
        logger.error("Failed to upload file '%s' to Google Drive: %s", filename, exc)
        await status_msg.edit_text(
            "❌ <b>Upload failed</b>\n\n"
            "I couldn't upload this file to Google Drive.\n"
            "Please try again.",
            parse_mode=ParseMode.HTML,
        )
    finally:
        # Always clean up temporary file in downloads/
        if local_temp_path.exists():
            try:
                local_temp_path.unlink()
            except OSError as cleanup_err:
                logger.warning("Could not delete temporary file '%s': %s", local_temp_path, cleanup_err)


@restricted
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages, including folder creation input."""
    message = update.effective_message
    if not message or not message.text:
        return

    text = message.text.strip()

    # Check if awaiting search input
    if context.user_data.pop("awaiting_search_keyword", False):
        await execute_search(update, context, text)
        return

    awaiting_folder = context.user_data.pop("awaiting_folder_name", None)

    if awaiting_folder:
        parent_id = awaiting_folder.get("parent_id", config.DRIVE_FOLDER_ID)
        for_upload = awaiting_folder.get("for_upload", False)

        is_valid, result = utils.sanitize_folder_name(text)
        if not is_valid:
            await message.reply_text(f"❌ {result}\nPlease try again.")
            context.user_data["awaiting_folder_name"] = awaiting_folder
            return

        folder_name = result
        await message.reply_text(f"Creating folder <b>{html.escape(folder_name)}</b>...", parse_mode=ParseMode.HTML)

        try:
            new_folder = await asyncio.to_thread(
                drive_service.default_drive_service.create_folder,
                name=folder_name,
                parent_id=parent_id,
            )
            new_folder_id = new_folder["id"]

            if for_upload and context.user_data.get("pending_upload"):
                keyboard = [
                    [InlineKeyboardButton("✅ Upload Here", callback_data=f"up:sel:{new_folder_id}")],
                    [InlineKeyboardButton("⬅️ Choose Another Folder", callback_data="up:choose_other")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="up:cancel")],
                ]
                await message.reply_text(
                    f"✅ <b>Folder created:</b> {html.escape(folder_name)}\n\n"
                    f"Upload pending file here?",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML,
                )
            else:
                await message.reply_text(
                    f"✅ <b>Folder created</b>\n\n📂 <b>{html.escape(folder_name)}</b>",
                    parse_mode=ParseMode.HTML,
                )
        except Exception as exc:
            logger.error("Failed to create folder '%s': %s", folder_name, exc)
            await message.reply_text("❌ Failed to create folder. Please try again.")


@restricted
async def mkdir_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /mkdir <folder_name> command."""
    message = update.effective_message
    if not message:
        return

    args = context.args or []
    if not args:
        await message.reply_text(
            "📁 <b>Usage:</b> <code>/mkdir &lt;folder name&gt;</code>\n\n"
            "Example: <code>/mkdir Banking Exams</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    raw_name = " ".join(args)
    is_valid, result = utils.sanitize_folder_name(raw_name)
    if not is_valid:
        await message.reply_text(f"❌ {result}")
        return

    folder_name = result
    parent_id = context.user_data.get("current_folder_id", config.DRIVE_FOLDER_ID)

    await message.reply_text(f"Creating folder <b>{html.escape(folder_name)}</b>...", parse_mode=ParseMode.HTML)

    try:
        await asyncio.to_thread(
            drive_service.default_drive_service.create_folder,
            name=folder_name,
            parent_id=parent_id,
        )
        await message.reply_text(
            f"✅ <b>Folder created</b>\n\n📂 <b>{html.escape(folder_name)}</b>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logger.error("Failed to create folder '%s': %s", folder_name, exc)
        await message.reply_text("❌ Failed to create folder. Please try again.")


# ==============================================================================
# BROWSE DRIVE & NAVIGATION
# ==============================================================================

MAX_TELEGRAM_SEND_SIZE = 50 * 1024 * 1024  # 50 MB


def build_browse_keyboard(
    folders: list[dict[str, Any]],
    files: list[dict[str, Any]],
    current_folder_id: str,
    parent_id: Optional[str],
    page: int = 1,
    page_size: int = 10,
) -> InlineKeyboardMarkup:
    """Build paginated inline keyboard for browsing Drive folders and files."""
    # Combine folders and files, folders first
    all_items: list[tuple[str, dict[str, Any]]] = [("folder", f) for f in folders] + [("file", f) for f in files]
    paged_items, current_page, total_pages = utils.paginate_items(all_items, page=page, page_size=page_size)

    buttons: list[list[InlineKeyboardButton]] = []

    for item_type, item in paged_items:
        item_id = item["id"]
        if item_type == "folder":
            label = utils.truncate_button_text(f"📂 {item['name']}", 32)
            buttons.append([InlineKeyboardButton(label, callback_data=f"br:o:{item_id}")])
        else:
            size_str = utils.format_file_size(item.get("size"))
            label = utils.truncate_button_text(f"📄 {item['name']} — {size_str}", 32)
            buttons.append([InlineKeyboardButton(label, callback_data=f"fi:v:{item_id}")])

    # Pagination controls
    nav_row: list[InlineKeyboardButton] = []
    if current_page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"br:p:{current_page - 1}"))
    if total_pages > 1:
        nav_row.append(InlineKeyboardButton(f"{current_page}/{total_pages}", callback_data="br:noop"))
    if current_page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"br:p:{current_page + 1}"))
    if nav_row:
        buttons.append(nav_row)

    # Directory navigation buttons
    dir_row: list[InlineKeyboardButton] = []
    if current_folder_id != config.DRIVE_FOLDER_ID:
        back_target = parent_id or config.DRIVE_FOLDER_ID
        dir_row.append(InlineKeyboardButton("⬅️ Back", callback_data=f"br:b:{back_target}"))
        dir_row.append(InlineKeyboardButton("🏠 Root", callback_data="br:r"))
    dir_row.append(InlineKeyboardButton("🔄 Refresh", callback_data=f"br:ref:{current_folder_id}"))
    buttons.append(dir_row)

    # Actions row
    action_row = [
        InlineKeyboardButton("➕ New Folder", callback_data=f"br:mkdir:{current_folder_id}"),
        InlineKeyboardButton("🔎 Search", callback_data="br:search"),
    ]
    if current_folder_id != config.DRIVE_FOLDER_ID:
        action_row.append(InlineKeyboardButton("🗑 Delete Folder", callback_data=f"del:ask:{current_folder_id}"))
    buttons.append(action_row)

    return InlineKeyboardMarkup(buttons)


async def render_browse_view(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    folder_id: str,
    page: int = 1,
) -> None:
    """Render the Drive browser UI for a given folder and page."""
    # Boundary check
    is_valid = await asyncio.to_thread(
        drive_service.default_drive_service.is_within_managed_root, folder_id
    )
    if not is_valid:
        msg = "❌ Cannot access folders outside the managed Google Drive directory."
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        elif update.effective_message:
            await update.effective_message.reply_text(msg)
        return

    # Update state
    context.user_data["current_folder_id"] = folder_id
    context.user_data["current_page"] = page

    try:
        # Fetch metadata and children in background thread
        current_meta = await asyncio.to_thread(
            drive_service.default_drive_service.get_metadata, folder_id
        )
        folders, files = await asyncio.to_thread(
            drive_service.default_drive_service.list_children, folder_id
        )
    except Exception as exc:
        logger.error("Failed to load browse view for folder_id=%s: %s", folder_id, exc)
        err_text = "❌ Failed to load folder contents. Please try again."
        if update.callback_query:
            await update.callback_query.answer(err_text, show_alert=True)
        elif update.effective_message:
            await update.effective_message.reply_text(err_text)
        return

    folder_name = current_meta.get("name", "Root")
    parent_list = current_meta.get("parents", [])
    parent_id = parent_list[0] if parent_list else None

    # Construct text view
    lines = [
        "📁 <b>Google Drive Manager</b>\n",
        f"<b>Current folder:</b> {html.escape(folder_name)}\n",
    ]

    if folders:
        lines.append("<b>Folders:</b>")
        for f in folders[:5]:
            lines.append(f"📂 {html.escape(f['name'])}")
        if len(folders) > 5:
            lines.append(f"<i>...and {len(folders) - 5} more folders</i>")
        lines.append("")

    if files:
        lines.append("<b>Files:</b>")
        for f in files[:5]:
            size_str = utils.format_file_size(f.get("size"))
            lines.append(f"📄 {html.escape(f['name'])} <i>({size_str})</i>")
        if len(files) > 5:
            lines.append(f"<i>...and {len(files) - 5} more files</i>")
    elif not folders:
        lines.append("<i>(This folder is empty)</i>")

    text = "\n".join(lines)
    reply_markup = build_browse_keyboard(folders, files, folder_id, parent_id, page=page)

    if update.callback_query and update.callback_query.message:
        try:
            await update.callback_query.message.edit_text(
                text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            # Fallback if content was identical
            pass
    elif update.effective_message:
        await update.effective_message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
        )


@restricted
async def browse_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /list and /folders commands to browse current folder."""
    current_folder_id = context.user_data.get("current_folder_id", config.DRIVE_FOLDER_ID)
    await render_browse_view(update, context, folder_id=current_folder_id, page=1)


@restricted
async def browse_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle browsing navigation callbacks (br:*)."""
    query = update.callback_query
    if not query:
        return

    data = query.data or ""
    await query.answer()

    if data == "br:noop":
        return

    if data == "br:r":
        await render_browse_view(update, context, folder_id=config.DRIVE_FOLDER_ID, page=1)
        return

    if data.startswith("br:o:"):
        target_folder = data[len("br:o:") :]
        await render_browse_view(update, context, folder_id=target_folder, page=1)
        return

    if data.startswith("br:b:"):
        target_folder = data[len("br:b:") :]
        await render_browse_view(update, context, folder_id=target_folder, page=1)
        return

    if data.startswith("br:p:"):
        try:
            page = int(data[len("br:p:") :])
        except ValueError:
            page = 1
        current_folder = context.user_data.get("current_folder_id", config.DRIVE_FOLDER_ID)
        await render_browse_view(update, context, folder_id=current_folder, page=page)
        return

    if data.startswith("br:ref:"):
        target_folder = data[len("br:ref:") :]
        page = context.user_data.get("current_page", 1)
        await render_browse_view(update, context, folder_id=target_folder, page=page)
        return

    if data.startswith("br:mkdir:"):
        parent_id = data[len("br:mkdir:") :]
        context.user_data["awaiting_folder_name"] = {
            "parent_id": parent_id,
            "for_upload": False,
        }
        await query.message.reply_text(
            "📁 <b>Enter the new folder name:</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "br:search":
        context.user_data["awaiting_search_keyword"] = True
        await query.message.reply_text(
            "🔎 <b>Search Files</b>\n\n"
            "Send the keyword to search, or type <code>/search &lt;keyword&gt;</code>.",
            parse_mode=ParseMode.HTML,
        )
        return


# ==============================================================================
# FILE DETAILS & SEND BACK
# ==============================================================================

@restricted
async def file_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callbacks related to viewing and sending files (fi:*)."""
    query = update.callback_query
    if not query:
        return

    data = query.data or ""
    await query.answer()

    if data.startswith("fi:v:"):
        file_id = data[len("fi:v:") :]
        await render_file_details(update, context, file_id)
        return

    if data.startswith("fi:b:"):
        folder_id = data[len("fi:b:") :]
        page = context.user_data.get("current_page", 1)
        await render_browse_view(update, context, folder_id=folder_id, page=page)
        return

    if data.startswith("fi:s:"):
        file_id = data[len("fi:s:") :]
        await execute_send_file(update, context, file_id)
        return


async def render_file_details(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    file_id: str,
) -> None:
    """Show detailed metadata and action buttons for a single file."""
    # Boundary check
    is_valid = await asyncio.to_thread(
        drive_service.default_drive_service.is_within_managed_root, file_id
    )
    if not is_valid:
        if update.callback_query:
            await update.callback_query.answer("❌ File is outside managed Drive directory.", show_alert=True)
        return

    try:
        meta = await asyncio.to_thread(
            drive_service.default_drive_service.get_metadata, file_id
        )
    except Exception as exc:
        logger.error("Failed to fetch file metadata (id=%s): %s", file_id, exc)
        if update.callback_query:
            await update.callback_query.answer("❌ Failed to fetch file details.", show_alert=True)
        return

    name = meta.get("name", "Unknown")
    size_str = utils.format_file_size(meta.get("size"))
    mime_type = meta.get("mimeType", "Unknown")
    date_str = utils.format_datetime(meta.get("createdTime") or meta.get("modifiedTime"))
    web_link = meta.get("webViewLink") or drive_service.default_drive_service.build_drive_link(file_id)

    parents = meta.get("parents", [])
    parent_id = parents[0] if parents else config.DRIVE_FOLDER_ID
    parent_name = "Drive"
    try:
        parent_meta = await asyncio.to_thread(
            drive_service.default_drive_service.get_metadata, parent_id
        )
        parent_name = parent_meta.get("name", "Drive")
    except Exception:
        pass

    details_text = (
        "📄 <b>File Details</b>\n\n"
        f"<b>Name:</b> {html.escape(name)}\n"
        f"<b>Size:</b> {size_str}\n"
        f"<b>Type:</b> {html.escape(mime_type)}\n"
        f"<b>Uploaded:</b> {date_str}\n"
        f"<b>Folder:</b> {html.escape(parent_name)}\n\n"
        f'🔗 <a href="{web_link}">Open in Google Drive</a>'
    )

    buttons = [
        [InlineKeyboardButton("📤 Send File Here", callback_data=f"fi:s:{file_id}")],
        [InlineKeyboardButton("🔗 Open in Drive", url=web_link)],
        [InlineKeyboardButton("🗑 Delete", callback_data=f"del:ask:{file_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"fi:b:{parent_id}")],
    ]

    if update.callback_query and update.callback_query.message:
        await update.callback_query.message.edit_text(
            details_text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=ParseMode.HTML,
        )


async def execute_send_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    file_id: str,
) -> None:
    """Download file from Google Drive and deliver to Telegram if <= 50 MB."""
    query = update.callback_query
    message = query.message if query else update.effective_message
    if not message:
        return

    # Boundary check
    is_valid = await asyncio.to_thread(
        drive_service.default_drive_service.is_within_managed_root, file_id
    )
    if not is_valid:
        await message.reply_text("❌ File is outside managed Drive directory.")
        return

    meta = await asyncio.to_thread(
        drive_service.default_drive_service.get_metadata, file_id
    )
    filename = meta.get("name", "downloaded_file")
    raw_size = meta.get("size")
    file_size = int(raw_size) if raw_size else 0
    web_link = meta.get("webViewLink") or drive_service.default_drive_service.build_drive_link(file_id)

    # 50 MB limit guard
    if file_size > MAX_TELEGRAM_SEND_SIZE:
        await message.reply_text(
            "📦 <b>This file is larger than Telegram's send limit.</b>\n\n"
            f"<b>File:</b> {html.escape(filename)}\n"
            f"<b>Size:</b> {utils.format_file_size(file_size)}\n\n"
            "You can open it directly in Google Drive:\n"
            f'🔗 <a href="{web_link}">Open in Drive</a>',
            parse_mode=ParseMode.HTML,
        )
        return

    status_msg = await message.reply_text("⏳ <b>Downloading file from Google Drive...</b>", parse_mode=ParseMode.HTML)
    temp_dest = config.DOWNLOADS_DIR / f"send_{uuid.uuid4().hex}_{filename}"

    try:
        # Download from Drive in background thread
        await asyncio.to_thread(
            drive_service.default_drive_service.download_file,
            file_id=file_id,
            destination_path=temp_dest,
        )

        await status_msg.edit_text("📤 <b>Sending to Telegram...</b>", parse_mode=ParseMode.HTML)

        # Send via Telegram
        with open(temp_dest, "rb") as fh:
            await context.bot.send_document(
                chat_id=message.chat_id,
                document=fh,
                filename=filename,
                caption=f"📄 {filename} ({utils.format_file_size(file_size)})",
            )
        await status_msg.delete()

    except Exception as exc:
        logger.error("Failed to send file id=%s to Telegram: %s", file_id, exc)
        await status_msg.edit_text(
            "❌ <b>Failed to send file.</b>\n\n"
            f'You can open it in Drive: <a href="{web_link}">Drive Link</a>',
            parse_mode=ParseMode.HTML,
        )
    finally:
        # Guaranteed temporary file cleanup
        if temp_dest.exists():
            try:
                temp_dest.unlink()
            except OSError as cleanup_err:
                logger.warning("Could not delete temporary download file '%s': %s", temp_dest, cleanup_err)


# ==============================================================================
# SEARCH FILES
# ==============================================================================

@restricted
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /search <keyword> command."""
    message = update.effective_message
    if not message:
        return

    args = context.args or []
    if not args:
        context.user_data["awaiting_search_keyword"] = True
        await message.reply_text(
            "🔎 <b>Search Files</b>\n\n"
            "Please reply with the keyword you want to search, or use:\n"
            "<code>/search &lt;keyword&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    keyword = " ".join(args).strip()
    await execute_search(update, context, keyword)


async def execute_search(update: Update, context: ContextTypes.DEFAULT_TYPE, keyword: str) -> None:
    """Execute search query scoped to managed Drive hierarchy."""
    message = update.effective_message
    if not message:
        return

    status_msg = await message.reply_text(f"🔎 Searching for <b>{html.escape(keyword)}</b>...", parse_mode=ParseMode.HTML)

    try:
        results = await asyncio.to_thread(
            drive_service.default_drive_service.search_files, keyword
        )
    except Exception as exc:
        logger.error("Search failed for keyword '%s': %s", keyword, exc)
        await status_msg.edit_text("❌ Search encountered an error. Please try again.")
        return

    if not results:
        await status_msg.edit_text(
            f"🔎 <b>Search results for:</b> {html.escape(keyword)}\n\n"
            "No matching files found in your managed Google Drive.",
            parse_mode=ParseMode.HTML,
        )
        return

    buttons: list[list[InlineKeyboardButton]] = []
    lines = [
        f"🔎 <b>Search results for:</b> {html.escape(keyword)}\n",
        f"Found {len(results)} matching file(s):\n",
    ]

    for f in results[:15]:
        size_str = utils.format_file_size(f.get("size"))
        lines.append(f"📄 {html.escape(f['name'])} <i>({size_str})</i>")
        btn_label = utils.truncate_button_text(f"📄 {f['name']} — {size_str}", 32)
        buttons.append([InlineKeyboardButton(btn_label, callback_data=f"fi:v:{f['id']}")])

    buttons.append([InlineKeyboardButton("🏠 Back to Root", callback_data="br:r")])

    await status_msg.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML,
    )


# ==============================================================================
# DELETE (TRASH ONLY)
# ==============================================================================

@restricted
async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /delete command to select items to trash."""
    message = update.effective_message
    if not message:
        return

    current_folder_id = context.user_data.get("current_folder_id", config.DRIVE_FOLDER_ID)
    await message.reply_text(
        "🗑 <b>Delete Files / Folders</b>\n\n"
        "Select an item from the browser below to view details and delete it safely to Google Drive Trash:",
        parse_mode=ParseMode.HTML,
    )
    await render_browse_view(update, context, folder_id=current_folder_id, page=1)


@restricted
async def delete_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle delete confirmation and execution callbacks (del:*)."""
    query = update.callback_query
    if not query:
        return

    data = query.data or ""
    await query.answer()

    if data.startswith("del:ask:"):
        item_id = data[len("del:ask:") :]
        await prompt_delete_confirmation(update, context, item_id)
        return

    if data.startswith("del:yes:"):
        item_id = data[len("del:yes:") :]
        await execute_trash_item(update, context, item_id)
        return

    if data.startswith("del:no:"):
        parent_id = data[len("del:no:") :] if len(data) > len("del:no:") else config.DRIVE_FOLDER_ID
        await query.message.edit_text("❌ Deletion cancelled.")
        await render_browse_view(update, context, folder_id=parent_id or config.DRIVE_FOLDER_ID, page=1)
        return


async def prompt_delete_confirmation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    item_id: str,
) -> None:
    """Display confirmation prompt before moving an item to Trash."""
    query = update.callback_query
    if not query:
        return

    # Root folder protection
    if item_id == config.DRIVE_FOLDER_ID:
        await query.answer("❌ The root folder cannot be deleted.", show_alert=True)
        return

    # Boundary check
    is_valid = await asyncio.to_thread(
        drive_service.default_drive_service.is_within_managed_root, item_id
    )
    if not is_valid:
        await query.answer("❌ Cannot delete items outside the managed Google Drive directory.", show_alert=True)
        return

    try:
        meta = await asyncio.to_thread(
            drive_service.default_drive_service.get_metadata, item_id
        )
    except Exception as exc:
        logger.error("Failed to fetch item metadata for deletion (id=%s): %s", item_id, exc)
        await query.answer("❌ Could not retrieve item information.", show_alert=True)
        return

    name = meta.get("name", "Unknown item")
    mime_type = meta.get("mimeType", "")
    is_folder = (mime_type == "application/vnd.google-apps.folder")

    parents = meta.get("parents", [])
    parent_id = parents[0] if parents else config.DRIVE_FOLDER_ID

    buttons = [
        [
            InlineKeyboardButton("🗑 Yes, Delete", callback_data=f"del:yes:{item_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"del:no:{parent_id}"),
        ]
    ]

    if is_folder:
        confirm_text = (
            "⚠️ <b>Delete folder?</b>\n\n"
            f"📂 <b>{html.escape(name)}</b>\n\n"
            "This folder and its contents will be moved to Google Drive Trash.\n\n"
            "Continue?"
        )
    else:
        confirm_text = (
            "⚠️ <b>Are you sure?</b>\n\n"
            f"Delete:\n<b>{html.escape(name)}</b>\n\n"
            "This will move the item to Google Drive Trash.\n"
            "It will <b>NOT</b> be permanently deleted."
        )

    await query.message.edit_text(
        confirm_text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML,
    )


async def execute_trash_item(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    item_id: str,
) -> None:
    """Move file or folder to Google Drive Trash with boundary and root protection."""
    query = update.callback_query
    if not query:
        return

    # Strict root protection
    if item_id == config.DRIVE_FOLDER_ID:
        await query.message.edit_text("❌ The root folder cannot be deleted.")
        return

    # Boundary check
    is_valid = await asyncio.to_thread(
        drive_service.default_drive_service.is_within_managed_root, item_id
    )
    if not is_valid:
        await query.message.edit_text("❌ Cannot delete items outside the managed Google Drive directory.")
        return

    # Fetch name and parent before trashing
    name = "Item"
    parent_id = config.DRIVE_FOLDER_ID
    try:
        meta = await asyncio.to_thread(
            drive_service.default_drive_service.get_metadata, item_id
        )
        name = meta.get("name", "Item")
        parents = meta.get("parents", [])
        if parents:
            parent_id = parents[0]
    except Exception:
        pass

    await query.message.edit_text("🗑 <b>Moving to Trash...</b>", parse_mode=ParseMode.HTML)

    try:
        # Move to trash via API
        await asyncio.to_thread(
            drive_service.default_drive_service.trash_file, item_id
        )

        buttons = [
            [InlineKeyboardButton("📁 Back to Folder", callback_data=f"br:o:{parent_id}")]
        ]

        success_text = (
            "✅ <b>Deleted</b>\n\n"
            f"<b>{html.escape(name)}</b> has been moved to Google Drive Trash."
        )

        await query.message.edit_text(
            success_text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=ParseMode.HTML,
        )

    except PermissionError as perm_err:
        await query.message.edit_text(f"❌ {perm_err}")
    except Exception as exc:
        logger.error("Error moving item id=%s to trash: %s", item_id, exc)
        await query.message.edit_text("❌ Failed to move item to Trash. Please try again.")


