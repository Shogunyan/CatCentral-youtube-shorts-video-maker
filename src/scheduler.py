"""
scheduler.py — Orchestrates the full pipeline and manages daily scheduling.

Full pipeline per run:
  1. Scrape viral cat video candidates
  2. Download 5 clips (+ extras as fallbacks)
  3. Randomly assign ranking order
  4. Generate title/description/tags
  5. Build the ranking video
  6. Upload to YouTube
  7. Mark source videos as used

Progress is reported via an optional `reporter(percent, action, log_msg)` callable
so the TUI (or any other caller) can display live updates.
"""
import logging
import random
import signal
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

import schedule

from config import Config
from src.caption_gen import generate_caption, generate_caption_from_source, generate_copy_caption
from src.channel_copier import ChannelCopier
from src.downloader import Downloader
from src.scraper import VideoScraper
from src.uploader import YouTubeUploader
from src.video_editor import check_ffmpeg
from src.video_tracker import VideoTracker

# ── Channels to copy oldest→newest before falling back to the viral scraper ───
# To add a channel: "Add https://www.youtube.com/@Name/shorts to copier"
_COPY_CHANNELS = [
    "https://www.youtube.com/@FilipponeTamela/shorts",
    "https://www.youtube.com/@DailyDoseOfInternetCats/shorts",
    "https://www.youtube.com/@Catsyycute/shorts",
]

logger = logging.getLogger(__name__)

# Sentinel no-op reporter so _report() can always be called unconditionally
_NOOP: Callable = lambda pct, action, log="": None



class Pipeline:
    def __init__(
        self,
        config: Config,
        dry_run: bool = False,
        reporter: Callable | None = None,
    ):
        self.config = config
        self.dry_run = dry_run
        self._reporter = reporter or _NOOP
        self.scraper = VideoScraper(config)
        self.downloader = Downloader(config)
        self.uploader = YouTubeUploader(config) if not dry_run else None
        self.tracker = VideoTracker(config)

        # Seed channel copier with configured channels (no-ops if already added)
        self.copier = ChannelCopier(config)
        for ch_url in _COPY_CHANNELS:
            self.copier.add_channel(ch_url)

    # ── Reporter helper ───────────────────────────────────────────────────────

    def _report(self, percent: float, action: str, log_msg: str = "") -> None:
        if log_msg:
            logger.info(f"[{percent:.0f}%] {log_msg}")
        else:
            logger.info(f"[{percent:.0f}%] {action}")
        self._reporter(percent, action, log_msg)

    # ── Main run ──────────────────────────────────────────────────────────────

    def run(self) -> bool:
        """Execute one full pipeline run. Returns True on success."""
        run_id = uuid.uuid4().hex[:8]
        dry = "[DRY RUN] " if self.dry_run else ""
        n = self.config.clips_per_video

        self._report(1, f"🚀  {dry}Starting pipeline…",
                     f"Pipeline run {run_id} starting")

        # ── 0a. Channel-copy mode — highest priority ──────────────────────────
        copy_item = self.copier.get_next_video()
        if copy_item:
            return self._run_channel_copy(copy_item, run_id)

        # ── 0. Housekeeping — reset expired clip counters ─────────────────────
        reset_count = self.scraper.reset_expired_clips()
        if reset_count:
            self._report(2, "♻️  Clip counters reset",
                         f"♻️  {reset_count} clip(s) recycled back into the pool (>14 days old)")

        # ── 1. Scrape — title is generated AFTER download so it can match source ─
        self._report(3, "🔍  Scraping viral cat videos…",
                     "Searching for 50K+ view cat ranking Shorts to clone…")
        candidates = self.scraper.get_candidates(want=n * 5)
        if not candidates:
            self._report(5, "❌  Scraping failed",
                         "No candidates found — check internet connection")
            return False
        self._report(15, "🔍  Scraping complete",
                     f"Found {len(candidates)} candidate videos")

        # ── Full-short mode: download 1 source video, overlay our branding ────────
        if candidates[0].get("_full_short"):
            full_item = candidates[0]
            rank_segments = full_item.get("_rank_segments", [])

            slug = (full_item.get("title") or "ranking Short")[:55]
            self._report(18, "⬇  Downloading ranking Short…", f"↓ [FULL_SHORT] {slug}")
            source_path = self.downloader.download(full_item)
            if not source_path:
                self._report(18, "❌  Download failed", "Could not download ranking Short")
                return False
            kb = source_path.stat().st_size // 1024
            self._report(44, "⬇  Downloaded", f"✓ Source Short downloaded ({kb} KB)")

            # ── Generate title NOW — after download — so it mirrors the source ──
            source_title = full_item.get("title", "")
            caption = generate_caption_from_source(source_title, n)
            title = caption["title"]
            self._report(45, "✏  Title generated", f"Title: {title}")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = self.config.processed_dir / f"ranking_{ts}_{run_id}.mp4"

            total_video_steps = 3
            video_step = [0]

            def on_video_step(step_msg: str) -> None:
                video_step[0] += 1
                pct = 52 + int(video_step[0] / total_video_steps * 36)
                self._report(min(pct, 88), f"🎬  {step_msg}", step_msg)

            self._report(52, "🎬  Building ranking video…", "Blurring original overlays + rendering CatCentral branding…")
            try:
                if not self.dry_run:
                    from src.video_editor import create_full_short_ranking_video
                    create_full_short_ranking_video(
                        source_path=source_path,
                        rank_segments=rank_segments,
                        title=title,
                        output_path=output_path,
                        config=self.config,
                        on_progress=on_video_step,
                    )
                else:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.touch()
            except Exception as e:
                self._report(52, "❌  Video creation failed", str(e))
                logger.error(f"Video creation failed: {e}", exc_info=True)
                return False

            # Upload + mark used (same as existing flow)
            self._report(90, "📤  Uploading to YouTube…", "Starting upload…")
            if self.dry_run:
                video_id = "DRY_RUN"
            else:
                video_id = self.uploader.upload(
                    video_path=output_path,
                    title=caption["title"],
                    description=caption["description"],
                    tags=caption["tags"],
                )
                if not video_id:
                    self._report(90, "❌  Upload failed", "YouTube upload returned no ID")
                    return False

            self.scraper.mark_used([full_item])
            self._report(100, "✅  Done! Video is live.", f"https://www.youtube.com/shorts/{video_id}")
            logger.info(f"Run {run_id} complete. video_id={video_id}")
            return True



    def _run_channel_copy(self, copy_item: dict, run_id: str) -> bool:
        """Download one video from a copy-channel, watermark it, and upload."""
        handle = copy_item.get("_channel_handle", "unknown")
        video_id = copy_item["id"]
        dry = "[DRY RUN] " if self.dry_run else ""

        self._report(5, f"📋  Copying from @{handle}…",
                     f"{dry}Channel copy: @{handle} / {video_id}")

        # Download
        self._report(10, "⬇  Downloading video…", f"↓ {copy_item['url']}")
        source_path = self.downloader.download(copy_item)
        if not source_path:
            self._report(10, "❌  Download failed", f"Could not download {video_id}")
            return False
        kb = source_path.stat().st_size // 1024
        self._report(40, "⬇  Downloaded", f"✓ Downloaded ({kb} KB)")

        caption = generate_copy_caption()
        title = caption["title"]
        self._report(45, "✏  Title generated", f"Title: {title}")

        # Apply watermark only (no blur, no title bar)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = self.config.processed_dir / f"copy_{ts}_{run_id}.mp4"
        self._report(50, "🎬  Adding watermark…", "Applying CatCentral watermark…")
        try:
            if not self.dry_run:
                from src.video_editor import apply_watermark_only
                apply_watermark_only(source_path, output_path, self.config)
            else:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.touch()
        except Exception as e:
            self._report(50, "❌  Watermark failed", str(e))
            logger.error(f"Watermark step failed: {e}", exc_info=True)
            return False

        self._report(80, "📤  Uploading…", "Starting upload…")
        if self.dry_run:
            uploaded_id = "DRY_RUN"
        else:
            uploaded_id = None
            for attempt in range(1, 4):
                uploaded_id = self.uploader.upload(
                    video_path=output_path,
                    title=caption["title"],
                    description=caption["description"],
                    tags=caption["tags"],
                )
                if uploaded_id:
                    break
                if attempt < 3:
                    self._report(80, f"⏳  Upload attempt {attempt} failed — retrying…",
                                 f"Upload attempt {attempt}/3 failed, retrying in 15s…")
                    time.sleep(15)
            if not uploaded_id:
                self._report(80, "❌  Upload failed after 3 attempts",
                             "YouTube upload returned no ID after 3 tries")
                return False

        self.copier.mark_used(video_id)
        self._report(100, "✅  Done! Video is live.",
                     f"Channel copy complete — {video_id}")
        logger.info(f"Run {run_id} complete (channel copy). video_id={uploaded_id}")
        return True


# ── Headless scheduler (used by `python main.py schedule`) ───────────────────

class Scheduler:
    """Wraps the `schedule` library to run the pipeline at configured times."""

    def __init__(self, config: Config, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run

    def _job(self):
        logger.info(
            f"Scheduled job triggered at {datetime.now().strftime('%H:%M:%S')}"
        )
        pipeline = Pipeline(self.config, dry_run=self.dry_run)
        try:
            success = pipeline.run()
            if not success:
                logger.warning("Pipeline run returned failure — will retry at next slot")
        except Exception as e:
            logger.error(f"Pipeline run raised exception: {e}", exc_info=True)

    def start(self):
        """Register jobs and block forever (Ctrl+C to stop)."""
        check_ffmpeg()

        for t in self.config.upload_times:
            schedule.every().day.at(t).do(self._job)
            logger.info(f"Scheduled daily upload at {t}")

        logger.info(
            "Scheduler running. Upload times: "
            + ", ".join(self.config.upload_times)
            + "  (Ctrl+C to stop)"
        )

        def _shutdown(sig, frame):
            logger.info("Scheduler stopped.")
            sys.exit(0)

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        while True:
            schedule.run_pending()
            time.sleep(30)
