"""
Finds high-quality, single cat clips with strong viral potential.

Strategy: search for individual funny/surprising cat moments (NOT compilations),
score by view count, recency, and engagement signals, then return the best
unused candidates for this run.
"""

import subprocess
import json
import re
import math
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)

# Searches targeting single-clip, high-engagement cat content.
# These consistently surface shareable, reaction-worthy clips rather than
# low-energy compilation filler.
CAT_SEARCH_QUERIES = [
    "funny cat caught on camera short",
    "cat being weird hilarious short",
    "my cat reaction funny short",
    "cat does something unexpected",
    "hilarious cat moment short",
    "cat instant karma funny",
    "cat behavior unhinged short",
    "cat jumpscare funny short",
    "cat fails funny short clips",
    "best cat moment short video",
    "cat freaks out funny",
    "funny cat video 2024",
]

# Title words that correlate with high engagement (comments, shares)
HIGH_ENGAGEMENT_SIGNALS = [
    "caught", "reaction", "when", "pov", "why", "wait", "watch",
    "unbelievable", "crazy", "insane", "hilarious", "omg", "wow",
    "unexpected", "surprised", "gone wrong", "fail", "moment",
]

# Reject anything that won't drive the core "relatable cat owner" audience
BLOCKED_KEYWORDS = [
    # Compilations (we do single clips)
    "top 5", "top 10", "top 3", "ranked", "ranking", "countdown", "compilation",
    # Artificial/fake
    "ai generated", "animated", "filter", "deepfake", "green screen",
    "snapchat", "costume", "ai cat", "cgi",
    # Off-topic
    "music", "lofi", "asmr", "podcast", "episode", "part 1", "part 2",
    "sponsor", "collab", "congress", "lawyer", "zoom call",
    "kiffness",  # known music-cat channel (different audience)
    # NSFW
    "nude", "sex", "porn", "onlyfans",
]

CAT_KEYWORDS = ["cat", "cats", "kitten", "kittens", "feline", "meow", "kitty", "tabby"]


class VideoCandidate:
    def __init__(
        self,
        video_id: str,
        title: str,
        view_count: int,
        duration: int,
        upload_date: str,
        channel: str,
    ):
        self.video_id = video_id
        self.url = f"https://www.youtube.com/watch?v={video_id}"
        self.title = title
        self.view_count = view_count
        self.duration = duration
        self.upload_date = upload_date
        self.channel = channel
        self.score: float = 0.0

    def __repr__(self) -> str:
        return f"VideoCandidate({self.video_id!r}, views={self.view_count:,}, dur={self.duration}s)"


class VideoFinder:
    def __init__(self, config, used_clips: dict):
        self.config = config
        self.used_clips = used_clips

    def find_candidates(self, count: int = 10) -> List[VideoCandidate]:
        """Return the top N scored, unused, quality-passing candidates."""
        seen_ids: set = set()
        all_candidates: List[VideoCandidate] = []

        for query in CAT_SEARCH_QUERIES:
            try:
                results = self._search(query, max_results=20)
                for c in results:
                    if c.video_id not in seen_ids:
                        seen_ids.add(c.video_id)
                        all_candidates.append(c)
            except Exception as e:
                logger.debug(f"Search '{query}' failed: {e}")
            if len(all_candidates) >= 80:
                break

        if not all_candidates:
            logger.warning("Primary searches returned nothing, running broad fallback")
            all_candidates = self._fallback_search()

        filtered = [c for c in all_candidates if self._passes_filters(c)]
        for c in filtered:
            c.score = self._score(c)
        filtered.sort(key=lambda c: c.score, reverse=True)

        fresh = [c for c in filtered if not self._is_used(c.video_id)]
        logger.info(
            f"Finder: {len(all_candidates)} raw → {len(filtered)} filtered → {len(fresh)} unused"
        )
        return fresh[:count]

    def _search(self, query: str, max_results: int = 20) -> List[VideoCandidate]:
        cmd = [
            "yt-dlp",
            f"ytsearch{max_results}:{query} #shorts",
            "--flat-playlist",
            "--print",
            "%(id)s\t%(title)s\t%(view_count)s\t%(duration)s\t%(upload_date)s\t%(uploader)s",
            "--match-filter",
            "duration < 90",
            "--no-warnings",
            "--quiet",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return self._parse_print_output(result.stdout)

    def _fallback_search(self) -> List[VideoCandidate]:
        cmd = [
            "yt-dlp",
            "ytsearch60:funny cat 2024 short",
            "--flat-playlist",
            "--print",
            "%(id)s\t%(title)s\t%(view_count)s\t%(duration)s\t%(upload_date)s\t%(uploader)s",
            "--match-filter",
            "duration < 90",
            "--no-warnings",
            "--quiet",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        return self._parse_print_output(result.stdout)

    def _parse_print_output(self, output: str) -> List[VideoCandidate]:
        candidates = []
        for line in output.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 6:
                continue
            video_id, title, views_str, dur_str, upload_date, channel = parts[:6]
            try:
                views = int(views_str) if views_str and views_str != "NA" else 0
                duration = int(float(dur_str)) if dur_str and dur_str != "NA" else 0
                candidates.append(
                    VideoCandidate(video_id, title, views, duration, upload_date or "", channel)
                )
            except (ValueError, IndexError):
                continue
        return candidates

    def _passes_filters(self, c: VideoCandidate) -> bool:
        title_lower = c.title.lower()

        # Hard minimum view count (allow half threshold as floor)
        if c.view_count < self.config.min_source_views // 2:
            return False

        # Duration: must be a usable short clip
        if c.duration < 5 or c.duration > 90:
            return False

        if any(kw in title_lower for kw in BLOCKED_KEYWORDS):
            return False

        if not any(kw in title_lower for kw in CAT_KEYWORDS):
            return False

        return True

    def _score(self, c: VideoCandidate) -> float:
        score = 0.0
        title_lower = c.title.lower()

        # View count (log scale, 0-60 points)
        if c.view_count > 0:
            score += min(math.log10(c.view_count) * 10, 60)

        # Strong recency bonus: fresh content gets algorithm tailwind
        if c.upload_date:
            try:
                upload = datetime.strptime(c.upload_date, "%Y%m%d")
                days_old = (datetime.now() - upload).days
                if days_old < 7:
                    score += 30
                elif days_old < 30:
                    score += 20
                elif days_old < 90:
                    score += 10
            except ValueError:
                pass

        # Engagement signal bonus (one bonus max to avoid stacking)
        if any(sig in title_lower for sig in HIGH_ENGAGEMENT_SIGNALS):
            score += 8

        # Sweet-spot duration: 12-28s = perfect retention/completion
        if 12 <= c.duration <= 28:
            score += 20
        elif 8 <= c.duration <= 45:
            score += 10

        # Question/exclamation titles drive comments
        if "?" in c.title or "!" in c.title:
            score += 5

        # Prefer clips that cleared the full view threshold (not just the floor)
        if c.view_count >= self.config.min_source_views:
            score += 15

        return score

    def _is_used(self, video_id: str) -> bool:
        if video_id not in self.used_clips:
            return False
        try:
            used_at = datetime.fromisoformat(self.used_clips[video_id])
            return (datetime.now() - used_at).days < 14
        except (ValueError, KeyError):
            return False
