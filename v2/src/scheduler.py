"""
Pipeline orchestrator — ties Finder → Processor → Renderer → Uploader together.

Also owns the daily scheduling loop and the JSON state files that track
which clips have been used and what has been uploaded.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import schedule

from .finder import VideoFinder, VideoCandidate
from .processor import ClipProcessor
from .renderer import VideoRenderer
from .caption_engine import CaptionEngine
from .uploader import YouTubeUploader

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, config):
        self.config = config
        config.data_dir.mkdir(parents=True, exist_ok=True)
        config.download_dir.mkdir(parents=True, exist_ok=True)
        config.processed_dir.mkdir(parents=True, exist_ok=True)

        self._used = self._load_json(config.used_clips_path)
        self._perf = self._load_json(config.performance_path)

        self.finder = VideoFinder(config, self._used)
        self.processor = ClipProcessor(config)
        self.renderer = VideoRenderer(config)
        self.captions = CaptionEngine(config.anthropic_api_key)
        self.uploader = YouTubeUploader(config)

    # ─────────────────────────── public API ─────────────────────────── #

    def run(self) -> bool:
        """Full pipeline: find best clip → process → render → upload."""
        logger.info("=" * 52)
        logger.info("CatCentral v2 — pipeline start")
        logger.info("=" * 52)

        candidates = self.finder.find_candidates(count=6)
        if not candidates:
            logger.error("No candidates found — aborting")
            return False

        best = candidates[0]
        logger.info(
            f"Best candidate: '{best.title}' | {best.view_count:,} views | {best.duration}s"
        )

        for candidate in candidates:
            try:
                if self._process(candidate):
                    return True
            except Exception as e:
                logger.error(f"Candidate failed ({candidate.video_id}): {e}")
                continue

        logger.error("All candidates failed — no upload this run")
        return False

    def run_dry(self) -> bool:
        """
        Process a clip all the way to the rendered MP4 but skip the upload.
        Useful for testing edits and overlay appearance.
        Output saved to data/processed/dry_run_output.mp4
        """
        logger.info("DRY RUN — no upload will happen")

        candidates = self.finder.find_candidates(count=3)
        if not candidates:
            logger.error("No candidates found")
            return False

        for c in candidates:
            logger.info(f"  {c.video_id} | {c.view_count:,} views | score={c.score:.1f} | {c.title}")

        best = candidates[0]
        raw = self.processor.download(best.url, self.config.download_dir)
        if not raw:
            logger.error("Download failed")
            return False

        try:
            duration = self.processor.get_duration(raw)
            peak = self.processor.find_peak_moment(raw)
            target_dur = min(self.config.target_duration, duration, self.config.max_duration)

            scaled = self.config.download_dir / f"dry_scaled_{raw.stem}.mp4"
            if not self.processor.trim_and_scale(raw, scaled, peak, target_dur):
                logger.error("Trim/scale failed")
                return False

            setup = self.captions.generate_setup_text()
            reaction = self.captions.generate_reaction_text()
            title = self.captions.generate_title(context=best.title)

            out = self.config.processed_dir / "dry_run_output.mp4"
            ok = self.renderer.render(scaled, out, setup, reaction, self.config.watermark_text, target_dur)

            if ok:
                logger.info(f"Dry run output: {out}")
                logger.info(f"Title would be: {title}")
                logger.info(f"Setup: '{setup}' | Reaction: '{reaction}'")
            return ok

        finally:
            raw.unlink(missing_ok=True)
            scaled_path = self.config.download_dir / f"dry_scaled_{raw.stem}.mp4"
            scaled_path.unlink(missing_ok=True)

    def start_scheduler(self):
        """Start the blocking daily scheduler daemon."""
        for t in self.config.upload_times:
            t = t.strip()
            schedule.every().day.at(t).do(self.run)
            logger.info(f"Scheduled: {t}")

        logger.info("Scheduler running — Ctrl+C to stop")
        while True:
            schedule.run_pending()
            time.sleep(30)

    # ─────────────────────────── internals ──────────────────────────── #

    def _process(self, candidate: VideoCandidate) -> bool:
        logger.info(f"Processing: {candidate.url}")

        raw = self.processor.download(candidate.url, self.config.download_dir)
        if not raw:
            logger.warning("Download failed")
            return False

        try:
            duration = self.processor.get_duration(raw)
            if duration < 4:
                logger.warning(f"Clip too short ({duration:.1f}s)")
                return False

            target_dur = min(self.config.target_duration, duration, self.config.max_duration)
            peak = self.processor.find_peak_moment(raw)

            scaled = self.config.download_dir / f"scaled_{raw.stem}.mp4"
            logger.info(f"Trimming {target_dur:.0f}s from t={peak:.1f}s")
            if not self.processor.trim_and_scale(raw, scaled, peak, target_dur):
                logger.warning("Trim/scale failed")
                return False

            setup = self.captions.generate_setup_text()
            reaction = self.captions.generate_reaction_text()
            title = self.captions.generate_title(context=candidate.title)
            description = self.captions.generate_description(title)
            tags = self.captions.generate_tags()

            logger.info(f"Title: {title}")
            logger.info(f"Setup overlay: '{setup}'")
            logger.info(f"Reaction overlay: '{reaction}'")

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            out = self.config.processed_dir / f"catcentral_v2_{stamp}.mp4"

            logger.info("Rendering overlays…")
            if not self.renderer.render(scaled, out, setup, reaction, self.config.watermark_text, target_dur):
                logger.warning("Render failed")
                return False

            logger.info("Uploading…")
            video_id = self.uploader.upload(out, title, description, tags)
            if not video_id:
                logger.error("Upload failed")
                return False

            self._mark_used(candidate.video_id)
            self._record(video_id, candidate, title)
            logger.info(f"Done → https://youtube.com/shorts/{video_id}")
            return True

        finally:
            raw.unlink(missing_ok=True)
            scaled = self.config.download_dir / f"scaled_{raw.stem}.mp4"
            scaled.unlink(missing_ok=True)

    def _mark_used(self, video_id: str):
        self._used[video_id] = datetime.now().isoformat()
        self._save_json(self._used, self.config.used_clips_path)

    def _record(self, yt_id: str, candidate: VideoCandidate, title: str):
        uploads = self._perf.setdefault("uploads", [])
        uploads.append(
            {
                "uploaded_at": datetime.now().isoformat(),
                "youtube_id": yt_id,
                "title": title,
                "source_url": candidate.url,
                "source_views": candidate.view_count,
                "source_duration": candidate.duration,
            }
        )
        self._save_json(self._perf, self.config.performance_path)

    @staticmethod
    def _load_json(path: Path) -> dict:
        if path.exists():
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    @staticmethod
    def _save_json(data: dict, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
