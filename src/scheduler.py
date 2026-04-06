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
"""
import logging
import random
import signal
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import schedule

from config import Config
from src.caption_gen import generate_caption
from src.downloader import Downloader
from src.scraper import VideoScraper
from src.uploader import YouTubeUploader
from src.video_editor import check_ffmpeg, create_ranking_video

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, config: Config, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run
        self.scraper = VideoScraper(config)
        self.downloader = Downloader(config)
        self.uploader = YouTubeUploader(config) if not dry_run else None

    def run(self) -> bool:
        """
        Execute one full pipeline run.  Returns True on success.
        """
        run_id = uuid.uuid4().hex[:8]
        logger.info(f"{'[DRY RUN] ' if self.dry_run else ''}Pipeline run {run_id} starting …")

        n = self.config.clips_per_video

        # ── 1. Scrape ─────────────────────────────────────────────────────────
        logger.info("Step 1/6 — Scraping viral cat videos …")
        candidates = self.scraper.get_candidates(want=n * 3)
        if not candidates:
            logger.error("No candidates found. Aborting run.")
            return False

        # ── 2. Download ───────────────────────────────────────────────────────
        logger.info(f"Step 2/6 — Downloading up to {len(candidates)} candidates …")
        downloaded = self.downloader.download_batch(candidates, target=n)
        if len(downloaded) < n:
            logger.error(
                f"Only {len(downloaded)}/{n} clips downloaded. Aborting run."
            )
            return False

        # Trim to exactly n clips
        downloaded = downloaded[:n]

        # ── 3. Randomise ranking order ────────────────────────────────────────
        logger.info("Step 3/6 — Shuffling ranking order …")
        random.shuffle(downloaded)
        clip_paths = [path for _, path in downloaded]
        used_ids = [meta["id"] for meta, _ in downloaded]

        # ── 4. Generate caption ───────────────────────────────────────────────
        logger.info("Step 4/6 — Generating title & description …")
        caption = generate_caption(n)
        title = caption["title"]
        logger.info(f"  Title: {title!r}")

        # ── 5. Build ranking video ────────────────────────────────────────────
        logger.info("Step 5/6 — Building ranking video …")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = self.config.processed_dir / f"ranking_{ts}_{run_id}.mp4"

        try:
            if not self.dry_run:
                create_ranking_video(
                    clip_paths=clip_paths,
                    title=title,
                    output_path=output_path,
                    config=self.config,
                )
            else:
                logger.info(f"  [DRY RUN] Would write video to {output_path}")
                # Create a placeholder so the rest of the flow can be tested
                output_path.touch()
        except Exception as e:
            logger.error(f"Video creation failed: {e}", exc_info=True)
            return False

        # ── 6. Upload ─────────────────────────────────────────────────────────
        logger.info("Step 6/6 — Uploading to YouTube …")
        if self.dry_run:
            logger.info(
                f"  [DRY RUN] Would upload '{title}' from {output_path}"
            )
            video_id = "DRY_RUN"
        else:
            video_id = self.uploader.upload(
                video_path=output_path,
                title=caption["title"],
                description=caption["description"],
                tags=caption["tags"],
            )
            if not video_id:
                logger.error("Upload failed.")
                return False

        # ── Mark used ─────────────────────────────────────────────────────────
        self.scraper.mark_used(used_ids)
        logger.info(
            f"Run {run_id} complete. Video ID: {video_id}. "
            f"Marked {len(used_ids)} source videos as used."
        )
        return True


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
            f"Scheduler running. Next jobs: "
            + ", ".join(self.config.upload_times)
            + "  (Ctrl+C to stop)"
        )

        # Graceful shutdown
        def _shutdown(sig, frame):
            logger.info("Scheduler stopped.")
            sys.exit(0)

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        while True:
            schedule.run_pending()
            time.sleep(30)
