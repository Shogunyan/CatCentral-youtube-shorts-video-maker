"""
downloader.py — Downloads video files from URLs using yt-dlp.

Priority rules:
  • YouTube  → best vertical (Shorts) quality ≤ 1080p
  • TikTok   → watermark-free format where available, else best quality
  • Instagram→ best quality via yt-dlp
"""
import logging
import re
import shutil
from pathlib import Path

import yt_dlp

logger = logging.getLogger(__name__)

# Minimum acceptable clip duration in seconds
MIN_DURATION = 4


class Downloader:
    def __init__(self, config):
        self.config = config
        self.out_dir: Path = config.download_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ─────────────────────────────────────────────────────────────

    def download(self, video: dict) -> Path | None:
        """
        Download a single video dict (from scraper) to disk.
        Returns the local file path on success, None on failure.
        """
        platform = video.get("platform", "unknown")
        url = video["url"]
        vid_id = self._sanitize_id(video["id"])

        # Check if already downloaded
        existing = self._find_existing(vid_id)
        if existing:
            logger.debug(f"Already downloaded: {existing.name}")
            return existing

        out_template = str(self.out_dir / f"{vid_id}.%(ext)s")
        opts = self._build_ydl_opts(platform, out_template)

        logger.info(f"Downloading {platform} video {vid_id} …")
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    logger.warning(f"yt-dlp returned no info for {url}")
                    return None

                # Validate duration
                duration = info.get("duration") or 0
                if duration and duration < MIN_DURATION:
                    logger.warning(f"Clip too short ({duration}s), skipping {vid_id}")
                    self._cleanup(vid_id)
                    return None

            downloaded = self._find_existing(vid_id)
            if downloaded:
                logger.info(f"  ✓ {downloaded.name} ({_fmt_size(downloaded)})")
                return downloaded
            else:
                logger.warning(f"Download completed but file not found for {vid_id}")
                return None

        except yt_dlp.utils.DownloadError as e:
            logger.warning(f"Download failed for {url}: {e}")
            self._cleanup(vid_id)
            return None
        except Exception as e:
            logger.error(f"Unexpected error downloading {url}: {e}")
            self._cleanup(vid_id)
            return None

    def download_batch(self, videos: list[dict], target: int) -> list[tuple[dict, Path]]:
        """
        Download from `videos` list until we have `target` successful clips.
        Returns list of (video_meta, local_path) tuples.
        """
        results: list[tuple[dict, Path]] = []
        for video in videos:
            if len(results) >= target:
                break
            path = self.download(video)
            if path:
                results.append((video, path))
        if len(results) < target:
            logger.warning(
                f"Only got {len(results)}/{target} clips after trying {len(videos)} candidates"
            )
        return results

    # ── yt-dlp options per platform ───────────────────────────────────────────

    def _build_ydl_opts(self, platform: str, out_template: str) -> dict:
        base = {
            "outtmpl": out_template,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "merge_output_format": "mp4",
            "postprocessors": [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mp4",
                }
            ],
        }

        if platform == "youtube":
            # Prefer vertical / square formats for Shorts; fall back to best
            base["format"] = (
                "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]"
                "/bestvideo[height<=1080]+bestaudio"
                "/best[height<=1080]"
                "/best"
            )

        elif platform == "tiktok":
            # yt-dlp can fetch the no-watermark stream from TikTok's API
            base["format"] = "download_addr-0/play_addr-0/best"
            base["extractor_args"] = {
                "tiktok": {
                    "webpage_download": False,
                }
            }

        elif platform == "instagram":
            base["format"] = "best[ext=mp4]/best"

        else:
            base["format"] = "best[height<=1080]/best"

        return base

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _sanitize_id(self, vid_id: str) -> str:
        return re.sub(r"[^A-Za-z0-9_\-]", "_", vid_id)[:60]

    def _find_existing(self, vid_id: str) -> Path | None:
        for ext in ("mp4", "webm", "mkv", "mov"):
            p = self.out_dir / f"{vid_id}.{ext}"
            if p.exists() and p.stat().st_size > 10_000:
                return p
        return None

    def _cleanup(self, vid_id: str):
        for p in self.out_dir.glob(f"{vid_id}.*"):
            try:
                p.unlink()
            except Exception:
                pass


def _fmt_size(path: Path) -> str:
    size = path.stat().st_size
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / 1024 / 1024:.1f} MB"
