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
import shutil
import signal
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

import schedule

from config import Config
from src.caption_gen import generate_caption
from src.downloader import Downloader
from src.scraper import VideoScraper
from src.uploader import YouTubeUploader
from src.video_editor import check_ffmpeg, create_ranking_video
from src.video_tracker import VideoTracker

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

        # ── 0. Housekeeping — reset expired clip counters ─────────────────────
        reset_count = self.scraper.reset_expired_clips()
        if reset_count:
            self._report(2, "♻️  Clip counters reset",
                         f"♻️  {reset_count} clip(s) recycled back into the pool (>14 days old)")

        # ── 1. Pick theme + scrape matching clips ─────────────────────────────
        self._report(3, "✏  Picking video theme…",
                     "Choosing title and matching search terms")
        caption = generate_caption(n)
        title = caption["title"]
        self._report(4, "✏  Theme picked", f"Title: {title}")

        self._report(5, "🔍  Scraping viral cat videos…",
                     f"Searching for clips matching: {title}")
        candidates = self.scraper.get_candidates(
            want=n * 3,
            yt_queries=caption.get("yt_queries"),
            tt_hashtags=caption.get("tt_hashtags"),
        )
        if not candidates:
            self._report(5, "❌  Scraping failed",
                         "No candidates found — check internet connection")
            return False
        self._report(15, "🔍  Scraping complete",
                     f"Found {len(candidates)} candidate videos")

        # ── 2. Download — report per clip ─────────────────────────────────────
        self._report(18, f"⬇  Downloading clips (0/{n})…", "Starting downloads")
        downloaded: list[tuple[dict, Path]] = []

        for video in candidates:
            if len(downloaded) >= n:
                break
            done = len(downloaded)
            base_pct = 18 + (done / n) * 26
            slug = video.get("title", "untitled")[:55]
            platform = video.get("platform", "?").upper()
            self._report(
                base_pct,
                f"⬇  Downloading clip {done + 1}/{n}…",
                f"↓ [{platform}] {slug}",
            )
            path = self.downloader.download(video)
            if path:
                downloaded.append((video, path))
                kb = path.stat().st_size // 1024
                self._report(
                    18 + (len(downloaded) / n) * 26,
                    f"⬇  Downloading clips ({len(downloaded)}/{n})…",
                    f"✓ Clip {len(downloaded)}/{n} saved  ({kb} KB)",
                )

        if len(downloaded) < 3:
            self._report(18, "❌  Not enough clips downloaded",
                         f"Got {len(downloaded)} — need at least 3, aborting")
            return False

        if len(downloaded) < n:
            logger.info(f"Got {len(downloaded)}/{n} clips — proceeding with fewer")
            n = len(downloaded)

        downloaded = downloaded[:n]
        random.shuffle(downloaded)
        clip_paths = [p for _, p in downloaded]
        clip_platforms = [m.get("platform", "unknown") for m, _ in downloaded]
        used_metas = [m for m, _ in downloaded]

        # ── 3. Caption already generated above (before scraping) ────────────
        self._report(46, "✏  Caption ready",
                     f"Title: {title}")

        # ── 3b. TTS voiceover ─────────────────────────────────────────────────
        tts_audio = None
        tts_tmp: Path | None = None
        if self.config.tts_enabled and not self.dry_run:
            self._report(49, "🎙  Generating AI voiceover…",
                         f"Using voice: {self.config.tts_voice}")
            try:
                from src.tts import TTSGenerator
                tts_tmp = self.config.data_dir / f"tts_{run_id}"
                tts_gen = TTSGenerator(voice=self.config.tts_voice)
                tts_audio = tts_gen.generate_all(n, tts_tmp)
                self._report(50, "🎙  Voiceover ready", "AI voice clips generated")
            except Exception as e:
                self._report(50, "🎙  Voiceover skipped",
                             f"TTS failed (continuing without voice): {e}")
                logger.warning(f"TTS generation failed: {e}")
                tts_audio = None

        # ── 4. Build video ────────────────────────────────────────────────────
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = self.config.processed_dir / f"ranking_{ts}_{run_id}.mp4"

        # Video editor reports each step via this callback
        video_step = [0]
        # blur + process + optional tts mix per clip + title card + concat + watermark
        tts_mix_steps = n if tts_audio else 0
        total_video_steps = n * 2 + tts_mix_steps + 3

        def on_video_step(step_msg: str) -> None:
            video_step[0] += 1
            pct = 52 + int(video_step[0] / total_video_steps * 36)
            self._report(min(pct, 88), f"🎬  {step_msg}", step_msg)

        self._report(52, "🎬  Building ranking video…",
                     "Starting video processing — this takes 1–3 minutes")

        try:
            if not self.dry_run:
                create_ranking_video(
                    clip_paths=clip_paths,
                    title=title,
                    output_path=output_path,
                    config=self.config,
                    clip_platforms=clip_platforms,
                    on_progress=on_video_step,
                    tts_audio=tts_audio,
                )
            else:
                logger.info(f"[DRY RUN] Would write video to {output_path}")
                for i in range(total_video_steps):
                    on_video_step(f"[DRY RUN] Video step {i + 1}/{total_video_steps}")
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.touch()
        except Exception as e:
            self._report(52, "❌  Video creation failed", str(e))
            logger.error(f"Video creation failed: {e}", exc_info=True)
            return False
        finally:
            # Clean up TTS temp files regardless of success/failure
            if tts_tmp and tts_tmp.exists():
                shutil.rmtree(tts_tmp, ignore_errors=True)

        # ── 5. Upload ─────────────────────────────────────────────────────────
        self._report(90, "📤  Uploading to YouTube…",
                     "Starting resumable upload — may take a few minutes")

        if self.dry_run:
            self._report(99, "📤  [DRY RUN] Skipping upload",
                         f"Would upload: {output_path.name}")
            video_id = "DRY_RUN"
        else:
            video_id = self.uploader.upload(
                video_path=output_path,
                title=caption["title"],
                description=caption["description"],
                tags=caption["tags"],
            )
            if not video_id:
                self._report(90, "❌  Upload failed",
                             "YouTube upload returned no ID — check logs")
                return False

        # ── Done ──────────────────────────────────────────────────────────────
        self.scraper.mark_used(used_metas)

        # Record the upload so the tracker can manage the re-upload cycle
        if not self.dry_run and video_id and video_id != "DRY_RUN":
            self.tracker.record_upload(
                youtube_id=video_id,
                title=caption["title"],
                clip_ids=[m["id"] for m in used_metas],
                video_path=output_path,
            )

        self._report(
            100,
            "✅  Done!  Video is live on YouTube.",
            f"https://www.youtube.com/shorts/{video_id}",
        )
        logger.info(f"Run {run_id} complete. video_id={video_id}")

        # ── Post-run: check re-upload queue ───────────────────────────────────
        if not self.dry_run and self.uploader:
            try:
                self.tracker.check_and_reupload(
                    uploader=self.uploader,
                    caption_gen=generate_caption,
                    reporter=self._reporter,
                )
            except Exception as e:
                logger.warning(f"Re-upload check failed (non-fatal): {e}")

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
