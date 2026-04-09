"""
viral_db.py — Tracks clips that appear in multiple high-view ranking videos.

The core insight: if a cat clip appears in 5 different viral ranking videos
(each with 1M+ views), that clip is "proven viral content".  Prioritising
these clips maximises engagement because:

  • They're recognisable — viewers who have seen them before engage positively
    ("I love this one!")
  • They've been validated by OTHER creators' millions of views
  • YouTube's algorithm already associates them with cat/funny content

Persistent storage: data/viral_clips.json
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class ViralClipDB:
    """
    Persistent database of clips that have appeared in viral ranking videos.

    Each entry tracks:
      viral_score       — how many DIFFERENT viral ranking videos used this clip
      total_viral_views — combined views of all viral videos that featured it
      standalone_views  — the clip's own standalone view count (best seen so far)
      first_seen        — ISO timestamp of first detection
      last_seen         — ISO timestamp of most recent detection
    """

    def __init__(self, db_path: Path):
        self._path = db_path
        self._data: dict[str, dict] = self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> dict[str, dict]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except Exception:
                pass
        return {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2))

    # ── Write ─────────────────────────────────────────────────────────────────

    def record(
        self,
        clip_id: str,
        clip_url: str,
        clip_title: str,
        clip_views: int,
        viral_video_id: str,
        viral_video_views: int,
    ) -> None:
        """Record that *clip_id* appeared in the viral ranking video *viral_video_id*."""
        if not clip_id:
            return
        now = datetime.now(timezone.utc).isoformat()
        existing = self._data.get(clip_id, {})

        # Avoid double-counting the same (clip, ranking_video) pair
        seen_in: set[str] = set(existing.get("viral_video_ids", []))
        if viral_video_id and viral_video_id in seen_in:
            return
        if viral_video_id:
            seen_in.add(viral_video_id)

        self._data[clip_id] = {
            "clip_id":           clip_id,
            "url":               clip_url or existing.get("url", ""),
            "title":             clip_title or existing.get("title", ""),
            "standalone_views":  max(clip_views, existing.get("standalone_views", 0)),
            "viral_score":       len(seen_in),
            "total_viral_views": (
                existing.get("total_viral_views", 0) + (viral_video_views or 0)
            ),
            "viral_video_ids":   list(seen_in),
            "first_seen":        existing.get("first_seen") or now,
            "last_seen":         now,
        }
        self._save()

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_viral_score(self, clip_id: str) -> int:
        """Return how many different viral ranking videos used this clip."""
        return self._data.get(clip_id, {}).get("viral_score", 0)

    def get_top_clips(self, limit: int = 50) -> list[dict]:
        """
        Return clips sorted by viral_score desc, then standalone_views desc.
        These are the clips with the most "cross-channel validation".
        """
        clips = list(self._data.values())
        clips.sort(
            key=lambda c: (
                -c.get("viral_score", 0),
                -c.get("standalone_views", 0),
            )
        )
        return clips[:limit]

    def get_all(self) -> dict[str, dict]:
        return dict(self._data)

    def __len__(self) -> int:
        return len(self._data)
