"""
video_tracker.py — Tracks uploaded videos and manages the recycling schedule.

Two timers run from the moment a video is uploaded:

  14 days  → per-clip reuse counters reset in scraper.py (handled there)
  17.5 days → compiled video eligible for re-upload; YouTube is checked for
              100 k+ view milestone; if the file still exists it is re-uploaded
              automatically, otherwise a notification is shown.

View-count fetching requires the youtube.readonly scope.  If the stored OAuth
token predates that scope being added, the fetch will fail gracefully (the
re-upload still proceeds, just without the live view count).
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

REUPLOAD_DAYS    = 17.5    # 2.5 weeks before a compiled video can be re-uploaded
VIRAL_THRESHOLD  = 100_000 # view count worth noting in the log


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _from_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _days_since(iso_str: str | None) -> float | None:
    dt = _from_iso(iso_str)
    if dt is None:
        return None
    return (_utcnow() - dt).total_seconds() / 86_400


class VideoTracker:
    """
    Persists to  data/uploaded_videos.json.

    Schema per entry:
    {
      "<youtube_video_id>": {
        "title":          str,
        "uploaded_at":    ISO datetime (UTC),
        "clip_ids":       [str, ...],
        "video_path":     str,
        "view_count":     int,
        "last_checked":   ISO datetime | null,
        "reupload_done":  bool,
        "reupload_id":    str | null   ← YouTube ID of the re-upload
      }
    }
    """

    def __init__(self, config):
        self.config = config
        self._db: dict[str, dict] = self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _path(self) -> Path:
        return self.config.data_dir / "uploaded_videos.json"

    def _load(self) -> dict[str, dict]:
        p = self._path()
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                pass
        return {}

    def _save(self) -> None:
        p = self._path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self._db, indent=2))

    # ── Recording ─────────────────────────────────────────────────────────────

    def record_upload(
        self,
        youtube_id: str,
        title: str,
        clip_ids: list[str],
        video_path: Path,
    ) -> None:
        """Call immediately after a successful YouTube upload."""
        self._db[youtube_id] = {
            "title":         title,
            "uploaded_at":   _iso(_utcnow()),
            "clip_ids":      clip_ids,
            "video_path":    str(video_path),
            "view_count":    0,
            "last_checked":  None,
            "reupload_done": False,
            "reupload_id":   None,
        }
        self._save()
        logger.info(f"Tracker: recorded upload {youtube_id!r} — {title!r}")

    # ── Eligibility checks ────────────────────────────────────────────────────

    def _reupload_candidates(self) -> list[tuple[str, dict]]:
        """Videos that are ≥ REUPLOAD_DAYS old and haven't been re-uploaded yet."""
        out = []
        for yt_id, data in self._db.items():
            if data.get("reupload_done"):
                continue
            age = _days_since(data.get("uploaded_at"))
            if age is not None and age >= REUPLOAD_DAYS:
                out.append((yt_id, data))
        return out

    # ── YouTube view-count fetch ──────────────────────────────────────────────

    def _fetch_view_counts(self, uploader, yt_ids: list[str]) -> dict[str, int]:
        """
        Fetch current view counts from the YouTube Data API.
        Returns empty dict gracefully if the API call fails (e.g. missing scope).
        """
        if not yt_ids:
            return {}
        try:
            return uploader.get_video_stats(yt_ids)
        except Exception as e:
            logger.warning(
                f"Could not fetch view counts ({e}). "
                "If this persists, re-run setup to add youtube.readonly scope."
            )
            return {}

    # ── Main periodic check ───────────────────────────────────────────────────

    def check_and_reupload(
        self,
        uploader,
        caption_gen,
        reporter=None,
    ) -> list[str]:
        """
        Called at the end of each pipeline run.

        1. Find compiled videos that are ≥ 2.5 weeks old.
        2. Fetch their current YouTube view counts.
        3. Log any that hit 100k+ views.
        4. Re-upload the video file if it still exists on disk.
           If the file is gone, log a notice — the user can manually rerun.
        5. Mark re-uploaded entries as done.

        Returns list of new YouTube video IDs created by re-uploads.
        """
        candidates = self._reupload_candidates()
        if not candidates:
            return []

        def _log(msg: str) -> None:
            logger.info(msg)
            if reporter:
                reporter(0, msg, msg)

        _log(f"♻️  Checking {len(candidates)} video(s) for re-upload eligibility…")

        # Fetch view counts for all candidates at once
        yt_ids = [yt_id for yt_id, _ in candidates]
        view_counts = self._fetch_view_counts(uploader, yt_ids)

        new_ids: list[str] = []

        for yt_id, data in candidates:
            title = data.get("title", "unknown")
            age_days = _days_since(data.get("uploaded_at")) or 0
            views = view_counts.get(yt_id, data.get("view_count", 0))

            # Update stored view count
            self._db[yt_id]["view_count"] = views
            self._db[yt_id]["last_checked"] = _iso(_utcnow())
            self._save()

            view_str = f"{views:,}" if views else "unknown"
            milestone = " 🔥 (100k+ views!)" if views >= VIRAL_THRESHOLD else ""
            _log(
                f"📊  '{title[:45]}' — {view_str} views, "
                f"{age_days:.0f} days old{milestone}"
            )

            # Attempt re-upload
            video_path = Path(data.get("video_path", ""))
            if not video_path.exists():
                _log(
                    f"⚠️  Re-upload ready but file not found: {video_path.name}. "
                    "Run the pipeline once to generate a fresh video instead."
                )
                # Mark done anyway so we don't keep nagging
                self._db[yt_id]["reupload_done"] = True
                self._save()
                continue

            _log(f"♻️  Re-uploading '{title[:45]}'…")
            try:
                caption = caption_gen()
                new_id = uploader.upload(
                    video_path=video_path,
                    title=caption["title"],
                    description=caption["description"],
                    tags=caption["tags"],
                )
                if new_id:
                    self._db[yt_id]["reupload_done"] = True
                    self._db[yt_id]["reupload_id"] = new_id
                    self._save()
                    new_ids.append(new_id)
                    _log(f"✅  Re-uploaded → https://www.youtube.com/shorts/{new_id}")
                else:
                    _log(f"❌  Re-upload failed for '{title[:45]}' — will retry next run")
            except Exception as e:
                _log(f"❌  Re-upload error for '{title[:45]}': {e}")

        return new_ids

    # ── Startup summary (for TUI) ─────────────────────────────────────────────

    def get_startup_notifications(self) -> list[str]:
        """
        Return a list of human-readable notification strings to display in
        the dashboard log when the app starts.
        """
        lines = []

        for yt_id, data in self._db.items():
            title = data.get("title", "unknown")[:45]
            age = _days_since(data.get("uploaded_at"))
            views = data.get("view_count", 0)
            done = data.get("reupload_done", False)

            if age is None:
                continue

            if done:
                continue

            if age >= REUPLOAD_DAYS:
                view_str = f"{views:,} views — " if views else ""
                lines.append(
                    f"♻️  '{title}' is {age:.0f} days old "
                    f"({view_str}eligible for re-upload)"
                )
            elif views >= VIRAL_THRESHOLD:
                lines.append(
                    f"🔥  '{title}' hit {views:,} views! "
                    f"({REUPLOAD_DAYS - age:.1f} days until re-upload window)"
                )

        return lines
