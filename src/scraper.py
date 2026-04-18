"""
scraper.py — Finds cat ranking Shorts and returns them for download.

Strategy:
  1. Find cat ranking YouTube Shorts (≤90 s) with ≥ 50K views.
  2. Pick the first valid, unused one and return it as a full-short candidate.
  3. The downloader fetches the full video; video_editor blurs original
     overlays and adds CatCentral branding.
"""
import json
import logging
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yt_dlp

from .viral_db import ViralClipDB

logger = logging.getLogger(__name__)

MAX_CLIP_REUSE = 9999       # effectively unlimited — clips can always be re-used
RANKING_MIN_VIEWS = 50_000  # 50K+ views required to be considered
RANKING_ANALYSE_LIMIT = 15  # how many ranking vids to scan before giving up

RANKING_SOURCE_QUERIES = [
    "funniest cats ranked shorts",
    "cats ranked funniest to least funny",
    "ranking the funniest cat moments",
    "top funniest cats ranked",
    "cat ranking countdown funny shorts",
    "ranking funny cat clips shorts",
    "funniest cat shorts ranked",
    "best cat moments ranked funny",
    "top cat clips ranked funny shorts",
    "ranking iconic cat moments shorts",
    "cats ranked funny moments 2024",
    "cats ranked funny moments 2025",
    "viral cat moments ranked",
    "cats ranked worst to funniest",
    "funniest cat compilation ranked shorts",
    "ranking the best cat videos",
    "top 5 funniest cats shorts",
    "rank every cat moment shorts",
    "cat moments ranked funniest",
    "ranking viral cat clips",
]


def _is_unwanted(title: str) -> bool:
    """
    Return True if this video should be skipped.

    Blocks:
      • Ranking / compilation / reaction meta-content
      • Non-real-cat content: AI, CGI, filters, animations, costumes, Zoom calls,
        news clips where humans are using cat filters, etc.
      • Meta / editing content where the cat is incidental rather than the subject
    """
    if not title:
        return False
    t = title.lower()
    BLOCK = [
        # Ranking / reaction meta
        "try not to laugh", "react", "reaction",
        "ranked", "ranking", "worst to best", "tier list",
        "#1 to #", "top 10", "top 5", "top 20",
        # Not a real cat
        "cat filter", "cat face filter", "zoom filter", "snap filter",
        "snapchat", "cat costume", "cat suit", "dressed as cat",
        "cat mask", "cat ears filter",
        "ai cat", "ai generated", "ai video", "ai animation", "ai art",
        "animated cat", "cartoon cat", "cgi cat", "3d cat",
        "greenscreen", "green screen",
        # AI-generated video tools — catches "made with Sora", "Kling AI cat", etc.
        "made with ai", "made by ai", "created with ai", "ai created",
        "generated with ai", "ai made",
        "midjourney", "stable diffusion", "runway ml", "sora ai",
        "kling ai", "kling video", "pika labs", "gen-2 video",
        "ai film", "deepfake",
        # Human / political content that often slips through (e.g. Zoom-cat-filter
        # viral congressional hearing clip)
        "congress", "senator", "hearing", "politician", "lawyer",
        "zoom call", "zoom meeting", "video call", "on camera filter",
        # Meta / editing content — cat is incidental, not the primary subject
        "how i edit", "how to edit", "editing tutorial", "video editing",
        "premiere pro", "davinci resolve", "final cut pro", "sony vegas",
        "sound design", "audio edit", "meow edit", "meow remix",
        "voice changer", "voice effect", "sound effect tutorial",
        "screen record", "screen capture",
        # Music production / remix — cat is a sound source, not the subject
        # (e.g. The Kiffness, lofi cat beats, cat song collabs)
        "kiffness",
        "lofi cat", "lo-fi cat", "lofi beats", "lo-fi beats",
        "made a song", "made music", "cat song", "cat music",
        "cat remix", "remix with", "collab with my cat",
        "original song", "music video", "music production",
        # Compilations / multi-clip videos (we want single original moments)
        "compilation", "best of", "top moments", "funny moments",
        # Generic non-cat
        "dog", "hamster", "rabbit", "bird", "parrot",
        # Animated / cartoon content (broader catch)
        "animated", "animation", "cartoon", "anime", "pixar",
        "disney", "dreamworks", "cgi", "3d render",
        "vtuber", "virtual", "gacha", "roblox", "minecraft",
        "game", "gameplay", "gaming", "fortnite",
        # Profanity / inappropriate / NSFW
        "nsfw", "nude", "naked", "sex", "porn", "xxx",
        "onlyfans", "thot", "twerk", "stripper",
        "shit", "fuck", "bitch", "ass ", "damn",
        "poop", "pee ", "toilet", "litter box prank",
        "diarrhea", "vomit", "puke", "gross out",
        "gore", "blood", "dead cat", "animal abuse",
        "cruelty", "hurt", "injured", "abuse",
        # Gross / fetish / weird bodily-function content ("Foody fartsy" etc.)
        "fart", "farts", "farting", "farty", "fartsy",
        "fetish", "weird fetish", "gross", "disgusting",
        "burp", "belch", "scat", "piss",
        "yiff", "furry nsfw",
        # Non-cat content that slips through
        "parking ticket", "standup", "stand up", "comedian",
        "podcast", "interview", "news", "politics",
        "cooking", "recipe", "mukbang", "asmr eating",
    ]
    return any(kw in t for kw in BLOCK)


# Cat-related words that must appear in a video title for it to be accepted.
_CAT_WORDS = {
    "cat", "cats", "kitten", "kittens", "kitty", "kitties",
    "feline", "meow", "purring", "tabby", "calico", "nyan",
    "tomcat", "catty", "cattos", "catto",
}


def _is_cat_video(title: str) -> bool:
    """
    Return True only if the video title clearly contains a cat-related word.
    Uses whole-word matching to avoid false positives like 'education' or 'locate'.
    """
    if not title:
        return False
    words = set(re.findall(r"\b[a-z]+\b", title.lower()))
    return bool(words & _CAT_WORDS)


def _is_english(title: str) -> bool:
    """
    Return True if the title is written in Latin/English script.
    Rejects Cyrillic, CJK, Arabic, Devanagari, Thai, and other non-Latin scripts.
    Emoji-only or number-only titles are allowed (they're universal).
    """
    if not title:
        return True
    alpha_chars = [c for c in title if c.isalpha()]
    if not alpha_chars:
        return True   # emoji / number-only → OK
    # U+0000–U+024F covers Basic Latin through Latin Extended-B
    latin = sum(1 for c in alpha_chars if ord(c) < 0x0250)
    return (latin / len(alpha_chars)) >= 0.75


_RANKING_WORDS = {
    "ranked", "ranking", "countdown", "tier", "tierlist", "tier list",
    "top 5", "top5", "top 10", "top10", "#1", "number 1", "number one",
    "worst to best", "best to worst", "funniest", "funniest cats",
}

# Words that disqualify a ranking video even if it mentions cats.
# We only want PURE cat ranking videos — no mixed-animal content.
_RANKING_REJECT = {
    # Mixed-animal or non-cat content
    "dog", "dogs", "puppy", "puppies", "pup ",
    "hamster", "rabbit", "bird", "parrot", "horse",
    "monkey", "animal", "animals", "pet ", "pets ",
    # Human-only content
    "people", "human", "man ", "woman ", "kid ", "baby ",
    "parking", "ticket", "story", "comedy", "standup",
    "stand up", "comedian", "podcast", "interview",
    # Obvious long-form / music (never Shorts)
    "compilation", "collection", "kiffness",
    "hour", "hours", "playlist",
    # Specific TV shows / streaming brands
    "nickelodeon", "netflix", "hulu",
    "sam & cat", "sam&cat", "sitcom",
    "episode", "episodes", "season",
}


def _is_ranking_video(title: str) -> bool:
    """Return True only if the title is a CAT-ONLY ranking/countdown video."""
    if not title:
        return False
    t = title.lower()
    # Must mention cats (uses the full _CAT_WORDS set defined above)
    if not any(w in t for w in _CAT_WORDS):
        return False
    # Must be a ranking/countdown format
    if not any(w in t for w in _RANKING_WORDS):
        return False
    # Reject TV shows, mixed-animal, or long-form content
    if any(w in t for w in _RANKING_REJECT):
        return False
    return True


class VideoScraper:
    def __init__(self, config):
        self.config = config
        self._used: dict[str, dict] = self._load_used()
        self._viral_db = ViralClipDB(config.viral_clips_path)

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load_used(self) -> dict[str, dict]:
        p = self.config.used_videos_path
        if p.exists():
            try:
                data = json.loads(p.read_text())
                if isinstance(data, list):
                    return {vid_id: {"count": MAX_CLIP_REUSE} for vid_id in data}
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {}

    def _save_used(self) -> None:
        p = self.config.used_videos_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self._used, indent=2))

    def mark_used(self, video_metas: list[dict]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for meta in video_metas:
            vid_id = meta.get("id", "")
            if not vid_id:
                continue
            existing = self._used.get(vid_id, {})
            self._used[vid_id] = {
                "count":         existing.get("count", 0) + 1,
                "first_used_at": existing.get("first_used_at") or now,
                "last_used_at":  now,
                "url":           meta.get("url") or existing.get("url", ""),
                "start_time":    meta.get("start_time"),
                "end_time":      meta.get("end_time"),
                "platform":      meta.get("platform") or existing.get("platform", "unknown"),
                "title":         meta.get("title") or existing.get("title", ""),
                "view_count":    meta.get("view_count") or existing.get("view_count", 0),
            }
        self._save_used()

    def reset_expired_clips(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=14)
        reset = 0
        for vid_id, data in self._used.items():
            if data.get("count", 0) == 0:
                continue
            first_used = data.get("first_used_at")
            if not first_used:
                continue
            try:
                dt = datetime.fromisoformat(first_used)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt <= cutoff:
                    data["count"] = 0
                    data["first_used_at"] = None
                    reset += 1
            except Exception:
                pass
        if reset:
            self._save_used()
            logger.info(f"Reset reuse counters for {reset} clip(s) (>14 days old)")
        return reset

    def _is_used(self, vid_id: str) -> bool:
        return self._used.get(vid_id, {}).get("count", 0) >= MAX_CLIP_REUSE

    def _use_count(self, vid_id: str) -> int:
        return self._used.get(vid_id, {}).get("count", 0)

    # ── Low-level yt-dlp helpers ──────────────────────────────────────────────

    def _ydl_extract_flat(self, url: str, playlist_end: int = 20) -> list[dict]:
        """Run yt-dlp in flat-extract mode and return the entries list."""
        ydl_opts = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
            "playlistend": playlist_end,
            "ignoreerrors": True,
            "nocheckcertificate": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(url, download=False)
                if result:
                    return result.get("entries") or []
        except Exception as e:
            logger.debug(f"yt-dlp flat extract failed for {url}: {e}")
        return []

    def _ydl_get_info(self, url: str) -> dict | None:
        """Get full video metadata including chapters (no download).

        Retries once after a short pause — YouTube rate-limits rapid sequential
        info requests made right after a search burst.
        """
        opts = {
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "nocheckcertificate": True,
            "skip_download": True,
            "socket_timeout": 20,
        }
        for attempt in range(2):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if info:
                        return info
            except Exception as e:
                logger.debug(f"Failed to get info for {url} (attempt {attempt+1}): {e}")
            if attempt == 0:
                time.sleep(1.5)   # brief pause before retry
        return None
    # ── Ranking-video search ──────────────────────────────────────────────────

    def _find_ranking_videos(self, min_views: int = RANKING_MIN_VIEWS) -> list[dict]:
        """Search for cat ranking Shorts with at least min_views."""
        seen: set[str] = set()
        found: list[dict] = []

        queries = random.sample(RANKING_SOURCE_QUERIES, len(RANKING_SOURCE_QUERIES))
        for q in queries:
            if len(found) >= RANKING_ANALYSE_LIMIT * 3:
                break
            logger.info(f"  Searching ranking sources: '{q[:55]}'")
            try:
                # Append #shorts so YouTube's search algorithm returns Shorts preferentially
                entries = self._ydl_extract_flat(f"ytsearch30:{q} #shorts", playlist_end=30)
            except Exception as ex:
                logger.debug(f"Ranking search failed '{q}': {ex}")
                continue
            for e in entries:
                if not e:
                    continue
                vid_id = e.get("id", "")
                if not vid_id or vid_id in seen:
                    continue

                title = e.get("title", "")
                if not _is_cat_video(title) or not _is_english(title):
                    continue
                if not _is_ranking_video(title):
                    continue
                views = e.get("view_count") or 0
                if views and views < min_views:
                    continue
                flat_dur = e.get("duration") or 0
                if flat_dur and flat_dur > 180:
                    continue
                seen.add(vid_id)
                found.append({
                    "id":         vid_id,
                    # Use /shorts/ URL — yt-dlp and YouTube treat this as a Shorts request
                    "url":        f"https://www.youtube.com/shorts/{vid_id}",
                    "title":      title,
                    "view_count": views,
                })

        found.sort(key=lambda x: x["view_count"], reverse=True)
        logger.info(f"  Found {len(found)} cat ranking Shorts with ≥{min_views:,} views")
        return found

    # ── Main public API ───────────────────────────────────────────────────────

    def get_candidates(self, want: int = 25) -> list[dict]:
        """
        Find the best cat ranking Short and return it for direct download.

        No Gemini analysis — just validate title + duration, then hand off
        the full video to the downloader. The video is downloaded as-is,
        original text regions are blurred, and our watermark is added.
        """
        # ── Find ranking videos ───────────────────────────────────────────────
        logger.info(f"Searching for cat ranking Shorts with {RANKING_MIN_VIEWS:,}+ views…")
        ranking_vids = self._find_ranking_videos(min_views=RANKING_MIN_VIEWS)

        if not ranking_vids:
            logger.info("No 50K+ results — widening to 20K+…")
            ranking_vids = self._find_ranking_videos(min_views=20_000)
        if not ranking_vids:
            logger.info("Still none — widening to 10K+…")
            ranking_vids = self._find_ranking_videos(min_views=10_000)
        if not ranking_vids:
            logger.warning("Could not find any cat ranking videos — returning empty")
            return []

        # ── Pick the first valid, unused Short (≤90s, available) ────────────────
        for rv in ranking_vids[:RANKING_ANALYSE_LIMIT]:
            rv_id = rv["id"]
            # Skip Shorts we've already used (reset after 14 days by reset_expired_clips)
            if self._use_count(rv_id) > 0:
                logger.info(f"  Skipping {rv_id}: already used recently")
                continue
            logger.info(f"Checking: '{rv['title'][:60]}' ({rv['view_count']:,} views)")
            info = self._ydl_get_info(rv["url"])
            if not info:
                logger.info(f"  Skipping {rv_id}: unavailable or rate-limited")
                continue
            rv_duration = info.get("duration") or 0
            if not rv_duration or rv_duration > 180:
                logger.info(f"  Skipping {rv_id}: {rv_duration:.0f}s — not a Short")
                continue
            rv_views = info.get("view_count") or rv["view_count"]
            if not rv_views or rv_views < 1_000:
                logger.info(f"  Skipping {rv_id}: {rv_views} views — too low")
                continue
            logger.info(f"  ✓ Using '{rv['title'][:55]}' ({rv_views:,} views, {rv_duration:.0f}s)")
            return [{
                "id":             rv_id,
                "url":            rv["url"],
                "platform":       "full_ranking_short",
                "title":          rv["title"],
                "view_count":     rv_views,
                "_full_short":    True,
                "_rank_segments": [],
            }]

        logger.warning("No valid unused cat ranking Short found — pipeline will abort.")
        return []
