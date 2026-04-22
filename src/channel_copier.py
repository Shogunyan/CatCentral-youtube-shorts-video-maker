"""
channel_copier.py — Queue-based copier for specific YouTube channels.

Downloads videos oldest-to-newest, one per pipeline run, rotating through
channels in order.  Tracks used videos so nothing is repeated until the
entire catalogue (across all channels) is exhausted.  Automatically checks
for new uploads before declaring the queue empty.

To add a channel from the scheduler/pipeline:
    copier = ChannelCopier(config)
    copier.add_channel("https://www.youtube.com/@SomeChannel/shorts")
"""
import json
import logging
import re
from datetime import datetime
from pathlib import Path

import yt_dlp

logger = logging.getLogger(__name__)

_STATE_FILENAME = "channel_copier.json"


class ChannelCopier:
    def __init__(self, config):
        self.config = config
        self._path: Path = config.data_dir / _STATE_FILENAME
        self._state = self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def has_channels(self) -> bool:
        return bool(self._state["channels"])

    def get_next_video(self) -> dict | None:
        """
        Return a video dict for the next unused video in the rotation, or None
        if all channels are fully exhausted (caller should fall back to scraper).

        Rotates through channels: ch0 → ch1 → ch2 → ch0 → …
        Each call advances the pointer by one channel.
        """
        channels = self._state["channels"]
        if not channels:
            return None

        n = len(channels)
        start_idx = self._state["next_channel_idx"] % n

        # First pass — try each channel in rotation order
        for offset in range(n):
            idx = (start_idx + offset) % n
            ch = channels[idx]
            video_id = self._first_unused(ch)
            if video_id:
                self._state["next_channel_idx"] = (idx + 1) % n
                self._save()
                logger.info(f"  Channel copier: using {ch['handle']} / {video_id}")
                return self._make_video_dict(ch, video_id)

        # All channels exhausted — refresh and try once more
        logger.info("All channel videos used — checking for new uploads…")
        self._refresh_all()

        for offset in range(n):
            idx = (start_idx + offset) % n
            ch = channels[idx]
            video_id = self._first_unused(ch)
            if video_id:
                self._state["next_channel_idx"] = (idx + 1) % n
                self._save()
                logger.info(f"  Channel copier (post-refresh): using {ch['handle']} / {video_id}")
                return self._make_video_dict(ch, video_id)

        logger.info("No new channel videos found — falling back to regular scraper")
        return None

    def mark_used(self, video_id: str) -> None:
        """Mark a video ID as used so it won't be picked again."""
        for ch in self._state["channels"]:
            if video_id in ch["all_ids"] and video_id not in ch["used_ids"]:
                ch["used_ids"].append(video_id)
                self._save()
                return

    def add_channel(self, url: str) -> None:
        """
        Add a new channel to the copier queue and immediately fetch its
        full video list (oldest → newest).

        Safe to call with a URL that's already in the list — it will no-op.
        """
        url = url.rstrip("/")
        existing_urls = {ch["url"] for ch in self._state["channels"]}
        if url in existing_urls:
            logger.info(f"Channel already in copier: {url}")
            return

        handle = self._handle_from_url(url)
        ch: dict = {
            "url": url,
            "handle": handle,
            "all_ids": [],
            "used_ids": [],
            "last_refreshed": None,
        }
        self._fetch_channel(ch, refresh=False)
        self._state["channels"].append(ch)
        self._save()
        logger.info(
            f"Added channel {handle!r} — {len(ch['all_ids'])} videos queued"
        )

    def stats(self) -> list[dict]:
        """Return a summary of each channel's queue status."""
        out = []
        for ch in self._state["channels"]:
            used = len(ch["used_ids"])
            total = len(ch["all_ids"])
            out.append({
                "handle": ch["handle"],
                "total": total,
                "used": used,
                "remaining": total - used,
            })
        return out

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _first_unused(self, ch: dict) -> str | None:
        """Return the oldest unused video ID for this channel, or None."""
        used = set(ch["used_ids"])
        for vid_id in ch["all_ids"]:
            if vid_id not in used:
                return vid_id
        return None

    def _make_video_dict(self, ch: dict, video_id: str) -> dict:
        return {
            "id": video_id,
            "url": f"https://www.youtube.com/shorts/{video_id}",
            "platform": "channel_copy",
            "title": f"{ch['handle']} — {video_id}",
            "_channel_handle": ch["handle"],
            "_channel_url": ch["url"],
        }

    def _refresh_all(self) -> None:
        for ch in self._state["channels"]:
            self._fetch_channel(ch, refresh=True)
        self._save()

    def _fetch_channel(self, ch: dict, refresh: bool) -> None:
        """
        Fetch all video IDs from the channel using yt-dlp flat extraction.

        First fetch  → store IDs oldest-first (reverse of yt-dlp's order).
        Refresh      → append only new IDs (preserves existing oldest-first order).
        """
        url = ch["url"]
        logger.info(f"  Fetching video list for @{ch['handle']}…")
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": "in_playlist",
                "playlistend": 2000,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            entries = (info or {}).get("entries", []) or []
            fetched_ids = [e["id"] for e in entries if e and e.get("id")]

            if not refresh:
                # yt-dlp returns newest-first; reverse so index 0 = oldest
                ch["all_ids"] = list(reversed(fetched_ids))
            else:
                known = set(ch["all_ids"])
                new_ids = [i for i in fetched_ids if i not in known]
                if new_ids:
                    # New videos are newer → append to end of oldest→newest list
                    ch["all_ids"].extend(reversed(new_ids))
                    logger.info(f"    @{ch['handle']}: {len(new_ids)} new video(s) discovered")

            ch["last_refreshed"] = datetime.now().isoformat(timespec="seconds")
            remaining = len(ch["all_ids"]) - len(ch["used_ids"])
            logger.info(
                f"    @{ch['handle']}: {len(ch['all_ids'])} total, "
                f"{len(ch['used_ids'])} used, {remaining} remaining"
            )
        except Exception as e:
            logger.warning(f"  Could not fetch @{ch['handle']}: {e}")

    @staticmethod
    def _handle_from_url(url: str) -> str:
        m = re.search(r"@([^/]+)", url)
        return m.group(1) if m else url.rstrip("/").split("/")[-1]

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except Exception:
                pass
        return {"channels": [], "next_channel_idx": 0}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._state, indent=2))
