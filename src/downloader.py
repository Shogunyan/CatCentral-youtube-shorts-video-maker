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
import subprocess
from pathlib import Path

import yt_dlp

# Regex for parsing ffmpeg ebur128 loudness lines
_EBU_RE = re.compile(r't:\s+([\d.]+)\s+M:\s+([-\d.]+)')

logger = logging.getLogger(__name__)

# Minimum acceptable clip duration in seconds
MIN_DURATION = 4

# Minimum source resolution — clips below this height are too pixelated
# when upscaled to 1080×1920. 480p is the floor; 720p is ideal.
MIN_CLIP_HEIGHT = 480


def _probe_height(path: Path) -> int:
    """Return the height of the first video stream, or 0 on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=height",
                "-of", "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        val = result.stdout.strip()
        return int(val) if val.isdigit() else 0
    except Exception:
        return 0


class Downloader:
    def __init__(self, config):
        self.config = config
        self.out_dir: Path = config.download_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ─────────────────────────────────────────────────────────────

    def download(self, video: dict) -> Path | None:
        """
        Download a single video dict (from scraper) to disk.
        If the dict contains start_time/end_time, only that segment is downloaded.
        Returns the local file path on success, None on failure.
        """
        start_time = video.get("start_time")
        end_time = video.get("end_time")

        if start_time is not None and end_time is not None:
            return self._download_segment(
                video, float(start_time), float(end_time)
            )
        return self._download_full(video)

    def _download_full(self, video: dict) -> Path | None:
        """Download a complete video file."""
        platform = video.get("platform", "unknown")
        url = video["url"]
        vid_id = self._sanitize_id(video["id"])

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
                duration = info.get("duration") or 0
                if duration and duration < MIN_DURATION:
                    logger.warning(f"Clip too short ({duration}s), skipping {vid_id}")
                    self._cleanup(vid_id)
                    return None

            downloaded = self._find_existing(vid_id)
            if downloaded:
                h = _probe_height(downloaded)
                if h and h < MIN_CLIP_HEIGHT:
                    logger.warning(
                        f"Clip too low-res ({h}p < {MIN_CLIP_HEIGHT}p), skipping {vid_id}"
                    )
                    self._cleanup(vid_id)
                    return None
                logger.info(f"  ✓ {downloaded.name} ({_fmt_size(downloaded)})"
                            + (f"  [{h}p]" if h else ""))
                return downloaded
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

    def _download_segment(
        self, video: dict, start: float, end: float
    ) -> Path | None:
        """Download a specific time range (segment) from a longer video."""
        platform = video.get("platform", "youtube")
        url = video["url"]
        vid_id = self._sanitize_id(video["id"])

        existing = self._find_existing(vid_id)
        if existing:
            logger.debug(f"Already downloaded: {existing.name}")
            return existing

        out_template = str(self.out_dir / f"{vid_id}.%(ext)s")
        opts = self._build_ydl_opts(platform, out_template)
        # Download only the specified time range
        opts["download_ranges"] = yt_dlp.utils.download_range_func(
            chapters=None,
            ranges=[(start, end)],
        )
        opts["force_keyframes_at_cuts"] = True

        logger.info(
            f"Downloading segment {vid_id} [{start:.1f}s – {end:.1f}s] …"
        )
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    logger.warning(f"yt-dlp returned no info for {url}")
                    return None

            downloaded = self._find_existing(vid_id)
            if downloaded:
                # Check segment duration — yt-dlp can produce near-zero clips
                # for keyframe-aligned ranges that don't contain any frames.
                seg_dur = self._probe_duration(downloaded)
                if seg_dur and seg_dur < MIN_DURATION:
                    logger.warning(
                        f"Segment too short ({seg_dur:.1f}s < {MIN_DURATION}s), skipping {vid_id}"
                    )
                    self._cleanup(vid_id)
                    return None
                h = _probe_height(downloaded)
                if h and h < MIN_CLIP_HEIGHT:
                    logger.warning(
                        f"Segment too low-res ({h}p < {MIN_CLIP_HEIGHT}p), skipping {vid_id}"
                    )
                    self._cleanup(vid_id)
                    return None
                logger.info(f"  ✓ {downloaded.name} ({_fmt_size(downloaded)})"
                            + (f"  [{h}p]" if h else ""))
                return downloaded
            logger.warning(f"Segment download completed but file not found for {vid_id}")
            return None

        except yt_dlp.utils.DownloadError as e:
            logger.warning(f"Segment download failed for {url} [{start}–{end}]: {e}")
            self._cleanup(vid_id)
            return None
        except Exception as e:
            logger.error(f"Unexpected error downloading segment {url}: {e}")
            self._cleanup(vid_id)
            return None

    def download_batch(self, videos: list[dict], target: int) -> list[tuple[dict, Path]]:
        """
        Download from `videos` list until we have `target` successful clips.
        After each download, trim long clips to the detected peak action moment.
        Returns list of (video_meta, local_path) tuples.
        """
        results: list[tuple[dict, Path]] = []
        for video in videos:
            if len(results) >= target:
                break
            path = self.download(video)
            if path:
                self._trim_to_action(path)
                results.append((video, path))
        if len(results) < target:
            logger.warning(
                f"Only got {len(results)}/{target} clips after trying {len(videos)} candidates"
            )
        return results

    # ── Action-moment detection ────────────────────────────────────────────────

    def _probe_duration(self, path: Path) -> float:
        """Return the duration of a video file in seconds, or 0 on failure."""
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error",
                 "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, timeout=10,
            )
            return float(r.stdout.strip())
        except Exception:
            return 0.0

    def _detect_peak_audio(self, path: Path, duration: float) -> float | None:
        """
        Single-pass EBU R128 loudness scan to find the peak action moment.
        Returns the timestamp of the loudest 100ms window, or None on failure.
        The caller should start the clip 3s before this timestamp.
        """
        if duration <= 0:
            return None
        try:
            r = subprocess.run(
                ["ffmpeg", "-hide_banner",
                 "-i", str(path),
                 "-af", "ebur128=peak=true",
                 "-vn", "-f", "null", "-"],
                capture_output=True, text=True, timeout=120,
            )
            best_t   = 0.0
            best_m   = -999.0
            for line in r.stderr.split("\n"):
                m = _EBU_RE.search(line)
                if not m:
                    continue
                t      = float(m.group(1))
                lufs   = float(m.group(2))
                # Ignore silent head/tail (first/last 2s of typical cat clips
                # are often just ambient noise before anything happens)
                if t < 2 or t > duration - 2:
                    continue
                if lufs > best_m:
                    best_m = lufs
                    best_t = t
            if best_m > -999.0:
                logger.debug(
                    f"  Peak audio at {best_t:.1f}s ({best_m:.1f} LUFS) "
                    f"for {path.name}"
                )
                return best_t
        except Exception as e:
            logger.debug(f"Peak audio detection failed for {path.name}: {e}")
        return None

    def _trim_to_action(self, path: Path) -> None:
        """
        If the clip is significantly longer than clip_duration, detect the
        peak audio moment and trim to [peak - 3s, peak - 3s + clip_duration].
        Uses stream-copy (no re-encode) — fast and lossless for this stage.
        Modifies the file in place.  Silent failures are logged and ignored.
        """
        target = getattr(self.config, "clip_duration", 25)
        duration = self._probe_duration(path)
        # Only trim if the clip is more than 8s longer than the target
        if not duration or duration <= target + 8:
            return

        peak = self._detect_peak_audio(path, duration)
        if peak is None:
            return

        start = max(0.0, peak - 3.0)
        tmp   = path.with_name(f"__act_{path.name}")
        try:
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-ss", f"{start:.3f}", "-t", str(target),
                 "-i", str(path),
                 "-c", "copy", str(tmp)],
                check=True, capture_output=True, timeout=60,
            )
            path.unlink()
            tmp.rename(path)
            logger.info(
                f"  Action trim: {path.name} → "
                f"[{start:.1f}s – {start + target:.1f}s]  "
                f"(was {duration:.0f}s)"
            )
        except Exception as e:
            logger.debug(f"Action trim failed for {path.name}: {e}")
            tmp.unlink(missing_ok=True)

    # ── yt-dlp options per platform ───────────────────────────────────────────

    def _build_ydl_opts(self, platform: str, out_template: str) -> dict:
        base = {
            "outtmpl": out_template,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "nocheckcertificate": True,
            "merge_output_format": "mp4",
            # Hard timeout: abort if a single fragment stalls for >30s
            "socket_timeout": 30,
            "retries": 2,
            "fragment_retries": 2,
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
            # Prefer no-watermark download URL; fall through to best quality
            # Format IDs vary by yt-dlp version, so we chain several options
            base["format"] = (
                "download_addr-0/play_addr-0"
                "/bestvideo[ext=mp4]+bestaudio[ext=m4a]"
                "/best[ext=mp4]/best"
            )
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
