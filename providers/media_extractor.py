"""Public Media & Video Provider using authorized yt-dlp extraction engine."""

from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path
import re
import shutil
from typing import Any, Callable, Dict, Optional, Tuple
import urllib.parse

from providers.base import BaseProvider, DownloadResult, ProviderMetadata
import utils

logger = logging.getLogger(__name__)

# Try importing yt_dlp
try:
    import yt_dlp
    HAS_YT_DLP = True
except ImportError:
    HAS_YT_DLP = False
    logger.warning("yt-dlp is not installed. MediaExtractorProvider will be disabled.")


class MediaExtractorProvider(BaseProvider):
    """Provider for public authorized video and audio extraction (YouTube, Facebook Reels, etc.)."""

    name: str = "Media Extractor"

    YOUTUBE_DOMAINS = ("youtube.com", "youtu.be", "m.youtube.com")
    FACEBOOK_DOMAINS = ("facebook.com", "fb.watch", "m.facebook.com", "fb.me")
    OTHER_MEDIA_DOMAINS = (
        "vimeo.com", "soundcloud.com", "dailymotion.com", "tiktok.com", "instagram.com",
        "twitter.com", "x.com", "reddit.com", "redd.it", "threads.net", "pin.it", "pinterest.com",
        "twitch.tv", "bilibili.com", "streamable.com", "rumble.com", "archive.org"
    )

    def can_handle(self, url: str) -> bool:
        """Check if URL belongs to a recognized media hosting platform."""
        try:
            parsed = urllib.parse.urlsplit(url)
            host = (parsed.hostname or "").lower()
            if any(domain in host for domain in self.YOUTUBE_DOMAINS):
                return True
            if any(domain in host for domain in self.FACEBOOK_DOMAINS):
                return True
            if any(domain in host for domain in self.OTHER_MEDIA_DOMAINS):
                return True
            return False
        except Exception:
            return False

    def _get_platform_name(self, host: str) -> str:
        """Derive user-facing platform name from hostname."""
        host = host.lower()
        if "youtube" in host or "youtu.be" in host:
            return "YouTube (Public Media)"
        if "facebook" in host or "fb.watch" in host:
            return "Facebook Reel (Public)"
        if "instagram" in host:
            return "Instagram (Public Media)"
        if "tiktok" in host:
            return "TikTok (Public Video)"
        if "twitter" in host or "x.com" in host:
            return "Twitter/X (Public Video)"
        if "reddit" in host or "redd.it" in host:
            return "Reddit (Public Media)"
        if "threads" in host:
            return "Threads (Public Media)"
        if "pinterest" in host or "pin.it" in host:
            return "Pinterest (Public Video)"
        if "twitch" in host:
            return "Twitch (Clip/VOD)"
        if "streamable" in host:
            return "Streamable (Public Video)"
        if "rumble" in host:
            return "Rumble (Public Video)"
        if "bilibili" in host:
            return "Bilibili (Public Video)"
        if "soundcloud" in host:
            return "SoundCloud (Public Audio)"
        if "vimeo" in host:
            return "Vimeo (Public Video)"
        return "Public Media Host"

    def probe(self, url: str, timeout: float = 12.0) -> ProviderMetadata:
        """Inspect media URL and extract title, estimated size, and format without downloading."""
        if not HAS_YT_DLP:
            raise RuntimeError("Media extractor dependency (yt-dlp) is not available.")

        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or "media-host"
        platform_name = self._get_platform_name(host)

        ydl_opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": timeout,
            "noplaylist": True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    raise ValueError("Could not extract media info from URL.")

                raw_title = info.get("title") or info.get("id") or "media_file"
                ext = info.get("ext") or "mp4"
                safe_title = utils.sanitize_filename(f"{raw_title}.{ext}")

                size = info.get("filesize") or info.get("filesize_approx") or 0
                duration = info.get("duration") or 0
                thumbnail = info.get("thumbnail")

                # Determine MIME type
                mime_type = mimetypes.guess_type(safe_title)[0] or (
                    "audio/mpeg" if ext in ("mp3", "m4a", "flac", "ogg") else "video/mp4"
                )

                return ProviderMetadata(
                    url=url,
                    final_url=info.get("webpage_url") or url,
                    provider_name=platform_name,
                    filename=safe_title,
                    size_bytes=size,
                    mime_type=mime_type,
                    is_direct=True,
                    is_html=False,
                    host=host,
                    extra={
                        "title": raw_title,
                        "duration": duration,
                        "thumbnail": thumbnail,
                        "uploader": info.get("uploader"),
                    },
                )

        except yt_dlp.utils.DownloadError as exc:
            err_msg = str(exc).lower()
            if "drm" in err_msg or "widevine" in err_msg or "encrypted" in err_msg:
                raise PermissionError("This media is DRM-protected and cannot be extracted.") from exc
            if "login" in err_msg or "sign in" in err_msg or "private" in err_msg or "account" in err_msg:
                raise PermissionError("This media requires private login or is restricted to authenticated accounts.") from exc
            if "age" in err_msg:
                raise PermissionError("This media is age-restricted and requires an account to access.") from exc
            raise ValueError(f"Media extraction failed: {exc}") from exc
        except Exception as exc:
            logger.exception("Unexpected error during media probe for %s: %s", url, exc)
            raise ValueError(f"Could not inspect media link: {exc}") from exc

    def download(
        self,
        url: str,
        dest_path: Path | str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        max_bytes: int = 2000 * 1024 * 1024,
        timeout: float = 30.0,
        max_retries: int = 5,
    ) -> DownloadResult:
        """Stream media to disk using yt-dlp without transcoding."""
        if not HAS_YT_DLP:
            raise RuntimeError("Media extractor dependency (yt-dlp) is not available.")

        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or "media-host"
        platform_name = self._get_platform_name(host)

        # Output template in same directory
        output_template = str(dest.parent / f"{dest.stem}.%(ext)s")

        def progress_hook(d: Dict[str, Any]) -> None:
            if d.get("status") == "downloading":
                downloaded = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                if max_bytes and downloaded > max_bytes:
                    raise ValueError(f"Download exceeded maximum limit ({utils.format_file_size(max_bytes)}). Aborted.")
                if progress_callback:
                    progress_callback(downloaded, total)
            elif d.get("status") == "finished":
                if status_callback:
                    status_callback("⚡ Download complete, finalising file...")

        has_ffmpeg = bool(shutil.which("ffmpeg"))
        # If ffmpeg is available, combine best streams; otherwise fall back to single-stream mp4 to avoid crash
        format_spec = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best" if has_ffmpeg else "best[ext=mp4]/best"

        ydl_opts: Dict[str, Any] = {
            "outtmpl": output_template,
            "format": format_spec,
            "progress_hooks": [progress_hook],
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": timeout,
            "retries": max_retries,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    raise ValueError("Failed to extract media.")

                # Identify downloaded file path
                actual_filepath = None
                if "requested_downloads" in info:
                    for req in info["requested_downloads"]:
                        p = Path(req.get("filepath", ""))
                        if p.exists():
                            actual_filepath = p
                            break

                if not actual_filepath or not actual_filepath.exists():
                    # Fallback search for generated file matching dest.stem
                    candidates = list(dest.parent.glob(f"{dest.stem}.*"))
                    if candidates:
                        actual_filepath = candidates[0]

                if not actual_filepath or not actual_filepath.exists():
                    raise FileNotFoundError("Extracted media file was not found on disk.")

                file_size = actual_filepath.stat().st_size
                if max_bytes and file_size > max_bytes:
                    actual_filepath.unlink(missing_ok=True)
                    raise ValueError(f"Extracted media file ({utils.format_file_size(file_size)}) exceeded {utils.format_file_size(max_bytes)}.")

                mime_type = mimetypes.guess_type(actual_filepath.name)[0] or "video/mp4"

                return DownloadResult(
                    dest_path=actual_filepath,
                    downloaded_bytes=file_size,
                    mime_type=mime_type,
                    provider_name=platform_name,
                    filename=actual_filepath.name,
                )

        except yt_dlp.utils.DownloadError as exc:
            err_msg = str(exc).lower()
            if "drm" in err_msg or "widevine" in err_msg:
                raise PermissionError("This media is DRM-protected and cannot be downloaded.") from exc
            if "login" in err_msg or "sign in" in err_msg or "private" in err_msg:
                raise PermissionError("This media requires private login credentials or permissions.") from exc
            raise IOError(f"Media extraction failed: {exc}") from exc
