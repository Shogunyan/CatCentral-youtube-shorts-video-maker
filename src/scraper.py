"""
scraper.py — Discovers viral cat clips by WATCHING cat ranking Shorts.

Strategy:
  1. Find cat ranking YouTube Shorts (≤60 s) with ≥ 200 K views.
  2. Analyse up to RANKING_ANALYSE_LIMIT of them by literally watching the
     video — multi-frame Gemini Vision identifies the funny moments and their
     exact timestamps. We then slice those timestamps out of the ranking video.
  3. Take CLIPS_FROM_FIRST_RANKING clips from the #1 ranking video and
     CLIPS_FROM_SECOND_RANKING from the #2 ranking video (= 5 total).
  4. Fill remaining slots from cross-referenced clips, then reusable clips.

Source-clip detection (three routes per ranking video — no description parsing):
  Route A — Gemini Vision    → multi-frame analysis identifies funny timestamps;
                                clips are sliced directly from the ranking video.
  Route B — Chapter slicing  → slice ranking video at chapter boundaries.
  Route C — Even slicing     → last resort; divide ranking video into N segments.

All routes slice the ranking video itself — clips never come from external
YouTube videos parsed out of descriptions. This keeps sources on-theme and
avoids dragging in random non-ranking cat videos.
"""
import json
import logging
import os
import random
import re
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yt_dlp

from .viral_db import ViralClipDB

logger = logging.getLogger(__name__)


# ── Gemini API compatibility layer ───────────────────────────────────────────
# Supports both the new google-genai (1.x) and the legacy google-generativeai.

def _gemini_generate(api_key: str, model_name: str, parts: list) -> str:
    """
    Call Gemini with a list of mixed text/image parts.

    `parts` is a list where each element is either:
      - a str  (text)
      - a dict with keys "mime_type" and "data" (image bytes)

    Returns the model's response text.
    """
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        new_parts = []
        for p in parts:
            if isinstance(p, str):
                new_parts.append(types.Part.from_text(text=p))
            elif isinstance(p, dict) and "data" in p:
                new_parts.append(types.Part.from_bytes(
                    data=p["data"], mime_type=p.get("mime_type", "image/jpeg"),
                ))
            else:
                new_parts.append(p)
        resp = client.models.generate_content(model=model_name, contents=new_parts)
        return resp.text
    except ImportError:
        import google.generativeai as genai  # type: ignore[no-redef]
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        resp = model.generate_content(parts)
        return resp.text


# ── Comment timestamp parsing ─────────────────────────────────────────────────
# Matches timestamps like 0:45, 1:23, 12:34, 1:23:45 in comment text
_TS_RE = re.compile(r'\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b')


def _parse_comment_timestamps(
    comments: list[dict], duration: float
) -> list[float]:
    """
    Extract and rank timestamps from video comments.

    Comments like "0:45 😂" or "1:23 this one got me" are crowd-sourced
    markers for the funniest moments. Returns timestamps sorted by how many
    comments mention that time range (most popular first).
    """
    counts: dict[int, int] = {}
    for c in comments:
        text = (c.get("text") or "") + " " + (c.get("parent") or "")
        for m in _TS_RE.finditer(text):
            a, b = int(m.group(1)), int(m.group(2))
            third = m.group(3)
            total = (a * 3600 + b * 60 + int(third)) if third else (a * 60 + b)
            # Must be within the video and past the first 5 seconds
            if 5 <= total <= max(5, duration - 5):
                bucket = int((total // 4) * 4)   # 4-second buckets
                counts[bucket] = counts.get(bucket, 0) + 1

    # Only keep timestamps mentioned at least twice (avoid one-off noise)
    popular = [(ts, cnt) for ts, cnt in counts.items() if cnt >= 2]
    popular.sort(key=lambda x: x[1], reverse=True)
    return [float(ts) for ts, _ in popular]

MAX_CLIP_REUSE = 9999   # effectively unlimited — clips can always be re-used

# Seconds of source clip kept around each detected peak moment.
SEGMENT_TARGET_SECS = 28

# ── Ranking-video strategy ────────────────────────────────────────────────────
# Clone the single highest-viewed cat ranking Short we can find:
# Gemini watches it in full, detects every rank-transition boundary, and we
# re-assemble the exact same clips (same order, same timing) under our own
# branding (watermark + swapped text colors).

# Only mine ranking videos above this view threshold.
RANKING_MIN_VIEWS = 50_000   # 50K — broad enough to find fresh daily content

# Max ranking videos analysed.  With the clone strategy we only need 1 (the
# best one), but we scan a larger pool to ensure we find a fresh video.
RANKING_ANALYSE_LIMIT = 15

# Large, varied query set — we cast a wide net then filter by 1M+ views.
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

# Regex: fish YouTube video IDs out of description text
_YT_ID_RE = re.compile(
    r'(?:youtu\.be/|youtube\.com/(?:watch\?(?:[^&"]*&)*v=|shorts/|embed/))'
    r'([\w-]{11})'
)


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
    # Obvious long-form / music channels (never Shorts)
    "compilation", "collection", "kiffness",
    "hour", "hours", "playlist",
    # TV shows / media channels
    "nickelodeon", "disney", "netflix", "hulu", "amazon",
    "sam & cat", "sam&cat", "sitcom", "episode", "episodes",
    "season", "series", "show", "channel", "network",
}

# "cat" or "cats" must appear in the title for it to be a cat ranking video.
_CAT_WORDS = {"cat", "cats", "kitten", "kittens", "kitty", "kitties"}


def _is_ranking_video(title: str) -> bool:
    """Return True only if the title is a CAT-ONLY ranking/countdown video."""
    if not title:
        return False
    t = title.lower()
    # Must mention cats
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

    def _get_comment_timestamps(self, url: str, duration: float) -> list[float]:
        """
        Scrape the top comments of a YouTube video and extract mentioned
        timestamps. Returns a list of seconds sorted by popularity.

        This leverages crowd wisdom: if many viewers timestamp "1:23 💀", that
        moment is almost certainly the funniest part of the video.
        """
        opts = {
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "nocheckcertificate": True,
            "skip_download": True,
            "getcomments": True,
            "extractor_args": {
                "youtube": {
                    "comment_sort": ["top"],
                    "max_comments": ["120"],
                }
            },
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if not info:
                return []
            comments = info.get("comments") or []
            timestamps = _parse_comment_timestamps(comments, duration)
            logger.info(
                f"  Comment timestamps: {len(timestamps)} popular moments "
                f"from {len(comments)} comments"
            )
            return timestamps
        except Exception as e:
            logger.debug(f"Comment fetch failed for {url}: {e}")
            return []

    # ── Compilation-based clip extraction ─────────────────────────────────────

    def _clips_from_compilation(self, comp: dict) -> list[dict]:
        """
        Extract individual clip segments from a compilation video.
        Uses chapter markers if available; otherwise splits the video evenly.
        """
        url = comp["url"]
        vid_id = comp["id"]
        comp_title = comp.get("title", "")
        comp_views = comp.get("view_count", 0)

        logger.info(f"Getting chapters for: {comp_title[:60]}")
        info = self._ydl_get_info(url)
        if not info:
            return []

        chapters = info.get("chapters") or []
        duration = info.get("duration") or comp.get("duration") or 0
        view_count = info.get("view_count") or comp_views
        clips = []

        if chapters:
            logger.info(f"  ✓ {len(chapters)} chapters found")
            for ch in chapters:
                start = float(ch.get("start_time", 0))
                end = float(ch.get("end_time", start + SEGMENT_TARGET_SECS))
                # Guard: malformed chapter data can have start >= video duration
                if duration and start >= duration:
                    continue
                seg_len = end - start
                # Skip chapters that are too short (<3s) or too long (>45s per clip)
                if seg_len < 3 or seg_len > 45:
                    continue
                clip_id = f"{vid_id}_{int(start)}"
                if self._is_used(clip_id):
                    continue
                label = (ch.get("title") or "").strip()
                clips.append({
                    "id":         clip_id,
                    "url":        url,
                    "title":      label or comp_title[:20],
                    "start_time": start,
                    "end_time":   min(end, start + SEGMENT_TARGET_SECS),
                    "platform":   "youtube",
                    "view_count": view_count,
                    "like_count": info.get("like_count") or 0,
                    "duration":   min(seg_len, SEGMENT_TARGET_SECS),
                })
        else:
            if not duration or duration < 30:
                return []

            # Try comment timestamps first — crowd-sourced funny moment detection
            logger.info(f"  No chapters — scraping comments for timestamps…")
            comment_ts = self._get_comment_timestamps(url, duration)

            if comment_ts:
                # Use the most-mentioned timestamps as clip start points.
                # Spread them out so clips don't overlap (min 8s apart).
                selected: list[float] = []
                for ts in comment_ts:
                    if all(abs(ts - s) >= 8 for s in selected):
                        selected.append(ts)
                    if len(selected) >= 12:
                        break
                logger.info(
                    f"  Using {len(selected)} comment-voted timestamps as clip starts"
                )
                for ts in selected:
                    # Back up 3s so we see the setup before the punchline
                    start = max(0.0, ts - 3.0)
                    end = min(start + SEGMENT_TARGET_SECS, duration - 2)
                    clip_id = f"{vid_id}_{int(start)}"
                    if self._is_used(clip_id):
                        continue
                    clips.append({
                        "id":         clip_id,
                        "url":        url,
                        "title":      comp_title[:20],
                        "start_time": start,
                        "end_time":   end,
                        "platform":   "youtube",
                        "view_count": view_count,
                        "like_count": 0,
                        "duration":   end - start,
                        "_comment_voted": True,
                    })
            else:
                # Fallback: split evenly (skip first/last 10s intro/outro)
                logger.info(f"  No comments — splitting {duration:.0f}s into segments")
                usable_start = 10.0
                usable_end   = duration - 10.0
                usable_len   = usable_end - usable_start
                n_segs = min(12, max(2, int(usable_len / SEGMENT_TARGET_SECS)))
                seg_len = usable_len / n_segs
                for i in range(n_segs):
                    start = usable_start + i * seg_len
                    end   = start + min(seg_len, SEGMENT_TARGET_SECS)
                    clip_id = f"{vid_id}_{int(start)}"
                    if self._is_used(clip_id):
                        continue
                    clips.append({
                        "id":         clip_id,
                        "url":        url,
                        "title":      comp_title[:20],
                        "start_time": start,
                        "end_time":   end,
                        "platform":   "youtube",
                        "view_count": view_count,
                        "like_count": 0,
                        "duration":   end - start,
                    })

        return clips

    # ── Visual frame analysis (Claude Vision) ────────────────────────────────

    def _get_frame_at_timestamp(
        self, video_url: str, timestamp: float
    ) -> Path | None:
        """
        Download 4 seconds of video starting at `timestamp` and extract one
        frame.  Returns a Path to a JPEG file, or None on failure.

        Uses a temp directory that the caller is responsible for cleaning up.
        """
        try:
            tmp_dir  = Path(tempfile.mkdtemp(prefix="catcentral_frame_"))
            seg_path = tmp_dir / "seg.mp4"
            frame_path = tmp_dir / "frame.jpg"

            # Download the short segment via yt-dlp
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "outtmpl": str(seg_path),
                "format": "worst[ext=mp4]/worst",   # lowest quality — we only need one frame
                "download_ranges": yt_dlp.utils.download_range_func(
                    chapters=None,
                    ranges=[(max(0, timestamp), timestamp + 4)],
                ),
                "force_keyframes_at_cuts": True,
                "socket_timeout": 20,
                "merge_output_format": "mp4",
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])

            if not seg_path.exists():
                return None

            # Extract a single frame with ffmpeg
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-ss", "1", "-i", str(seg_path),
                 "-vframes", "1", "-q:v", "3", str(frame_path)],
                check=True, capture_output=True, timeout=20,
            )
            seg_path.unlink(missing_ok=True)
            return frame_path if frame_path.exists() else None
        except Exception as e:
            logger.debug(f"Frame extraction failed at {timestamp:.1f}s: {e}")
            return None

    def _describe_frame_gemini(
        self, frame_path: Path, api_key: str
    ) -> str | None:
        """
        Send a frame to Gemini Flash (vision) and get a concise description
        of the specific cat action — used as a YouTube search query.

        Returns a short string like 'cat falls off shelf' or None on failure.
        Free tier: 1 500 req/day at aistudio.google.com.
        """
        try:
            with open(frame_path, "rb") as f:
                img_bytes = f.read()

            text = _gemini_generate(api_key, "gemini-2.0-flash", [
                {"mime_type": "image/jpeg", "data": img_bytes},
                (
                    "This is a frame from a viral funny cat video. "
                    "Describe ONLY the cat's specific action or reaction in 3-6 words, "
                    "suitable as a YouTube search query to find the original clip. "
                    "Examples: 'cat falls off counter', 'kitten scared of cucumber', "
                    "'cat yells at owner'. Reply with just the phrase, nothing else."
                ),
            ])
            description = text.strip().lower()
            logger.info(f"  Gemini Vision: '{description}'")
            return description
        except Exception as e:
            logger.debug(f"Gemini Vision failed: {e}")
            return None

    def _extract_frames_for_analysis(
        self, video_url: str, duration: float, n_frames: int = 8
    ) -> list[tuple[float, bytes]]:
        """
        Extract N evenly-spaced frames from a video URL.
        Returns (timestamp_secs, jpeg_bytes) pairs for batch Gemini analysis.
        Skips the first/last 5% of the video (usually title cards / end screens).
        """
        results: list[tuple[float, bytes]] = []
        if duration < 5 or n_frames < 1:
            return results
        margin = max(1.0, duration * 0.05)
        usable = duration - 2 * margin
        if usable <= 0:
            return results
        timestamps = (
            [margin + usable * i / max(1, n_frames - 1) for i in range(n_frames)]
            if n_frames > 1 else [duration / 2]
        )
        for ts in timestamps:
            frame_path = self._get_frame_at_timestamp(video_url, ts)
            if frame_path:
                try:
                    with open(frame_path, "rb") as f:
                        results.append((ts, f.read()))
                except Exception:
                    pass
                finally:
                    # Must unlink the frame before rmdir — directory must be empty
                    frame_path.unlink(missing_ok=True)
                    try:
                        frame_path.parent.rmdir()
                    except Exception:
                        pass
        return results

    def _gemini_analyze_ranking_video(
        self,
        rv_url: str,
        rv_duration: float,
        chapters: list[dict],
        api_key: str,
    ) -> list[dict]:
        """
        Watch a ranking video frame-by-frame and detect exactly where each
        ranked cat segment starts and ends, by identifying visual rank
        indicators (on-screen numbers, title cards, overlays).

        Dense frame sampling (1 frame per ~5 s) lets Gemini see the actual
        rank numbers change on screen.  Chapter markers are supplied as
        anchors when available.  The returned segments are contiguous —
        end_time[N] == start_time[N+1] — so downloads cover the full clip
        without gaps or overlaps.
        """
        try:
            import json as _json

            # Dense sampling: ~1 frame every 2 s, capped at 60 frames.
            # Fine enough to catch every rank-number change on screen.
            n_frames = min(60, max(15, int(rv_duration / 2)))
            logger.info(
                f"    Gemini: extracting {n_frames} frames from "
                f"{rv_duration:.0f}s video to detect rank transitions…"
            )
            frame_data = self._extract_frames_for_analysis(rv_url, rv_duration, n_frames)
            if not frame_data:
                logger.debug("    Gemini: no frames extracted")
                return []

            # Chapter markers as anchor hints
            chapter_hint = ""
            if chapters:
                ch_str = "; ".join(
                    f"@{ch.get('start_time', 0):.0f}s '{(ch.get('title') or '').strip()}'"
                    for ch in chapters[:12]
                )
                chapter_hint = (
                    f"\nChapter anchors (use to verify your transition timestamps): {ch_str}"
                )

            frame_list = ", ".join(f"{t:.1f}s" for t, _ in frame_data)
            dur_s = str(int(rv_duration))

            prompt_parts: list = [
                f"You are watching a YouTube cat ranking video ({rv_duration:.0f}s long) "
                f"that plays cat clips from worst (rank 5) to best (rank 1)."
                f"{chapter_hint}\n\n"
                f"Frames sampled at: {frame_list}\n\n"
                "TASK: Identify the exact start and end time of each ranked cat segment "
                "by detecting when the rank NUMBER changes on screen.\n\n"
                "HOW TO SPOT RANK TRANSITIONS:\n"
                "• On-screen rank indicators: '5', '#5', 'No.5', 'Rank 5', big countdown "
                "numbers, title-card text showing the rank position\n"
                "• Visual cuts/fades/wipes between two different cat clips\n"
                "• A transition frame (black screen, flash, animated whoosh) signals "
                "the boundary between ranks\n"
                "• Rank goes HIGH → LOW: first segment is rank 5 (worst), last is rank 1 (best)\n\n"
                "For EACH rank segment return one JSON object:\n"
                "  start_time: integer seconds — when THIS rank's clip begins\n"
                "  end_time: integer seconds — when THIS rank's clip ends "
                "(= start of NEXT rank, or video end)\n"
                "  rank: integer rank number shown (5=first/worst … 1=last/best)\n"
                "  screen_label: the EXACT text label shown on screen for this rank "
                "(e.g. '#5 WORST CAT', 'Rank 3', '2. ALMOST') — copy it verbatim, "
                "empty string if no text label is visible\n"
                "  description: 8-16 word description of what the cat does\n"
                "  is_real_cat: true only if a LIVE real cat (not animated, CGI, "
                "Zoom filter, or AI-generated)\n"
                "  is_animated: true if cartoon / CGI / animation\n"
                "  is_ai_generated: true if footage looks AI-generated "
                "(Sora, Kling, Runway, Pika — unnatural motion, dreamlike textures)\n"
                "  is_english: true if on-screen text is English or no text is visible\n"
                "  confidence: 1-10 how sure you are of the start_time / end_time\n\n"
                "STRICT RULES:\n"
                "• Segments must be CONTIGUOUS: end_time[N] == start_time[N+1]\n"
                "• NO overlaps, NO gaps between segments\n"
                "• First segment's start_time should be 0 (or just after any intro)\n"
                "• Last segment's end_time should be " + dur_s + " (or just before outro)\n"
                "• Each segment must be ≥ 3 seconds long\n"
                "• Timestamps must be between 0 and " + dur_s + "\n"
                "• Return segments in chronological order (rank 5 first → rank 1 last)\n"
                "• EXCLUDE segments where is_real_cat=false, is_animated=true, "
                "or is_ai_generated=true\n"
                "Reply ONLY with a valid JSON array — no markdown, no extra text.\n"
                'Example (5-clip ranking, 60 s video):\n'
                '[{"start_time":0,"end_time":11,"rank":5,'
                '"description":"orange tabby slides off kitchen counter",'
                '"is_real_cat":true,"is_animated":false,"is_ai_generated":false,'
                '"is_english":true,"confidence":9},\n'
                ' {"start_time":11,"end_time":23,"rank":4,'
                '"description":"kitten terrified by cucumber on floor",'
                '"is_real_cat":true,"is_animated":false,"is_ai_generated":false,'
                '"is_english":true,"confidence":8}]\n',
            ]
            for ts, frame_bytes in frame_data:
                prompt_parts.append(f"\n[Frame at {ts:.1f}s]:")
                prompt_parts.append({"mime_type": "image/jpeg", "data": frame_bytes})

            raw = _gemini_generate(api_key, "gemini-2.0-flash", prompt_parts).strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
            raw = re.sub(r"\s*```\s*$",        "", raw, flags=re.MULTILINE)
            bracket = raw.find("[")
            if bracket == -1:
                logger.debug("    Gemini: no JSON array in response")
                return []
            raw = raw[bracket:]

            clips_raw = _json.loads(raw)
            if not isinstance(clips_raw, list):
                clips_raw = [clips_raw]

            valid: list[dict] = []
            for item in clips_raw:
                if not isinstance(item, dict):
                    continue
                # Content filters
                if item.get("is_animated", False) or item.get("is_ai_generated", False):
                    continue
                if not item.get("is_real_cat", True):
                    continue
                if not item.get("is_english", True):
                    continue
                start = float(item.get("start_time", 0))
                end   = float(item.get("end_time", start + 20))
                if end <= start or (end - start) < 3:
                    continue
                start = max(0.0, min(start, rv_duration))
                end   = min(end, rv_duration)
                valid.append({
                    "start_time":          start,
                    "end_time":            end,
                    "rank":                int(item.get("rank", 0)),
                    "screen_label":        str(item.get("screen_label", "")).strip()[:40],
                    "description":         str(item.get("description", ""))[:120],
                    "is_real_cat":         bool(item.get("is_real_cat", True)),
                    "is_animated":         bool(item.get("is_animated", False)),
                    "is_ai_generated":     bool(item.get("is_ai_generated", False)),
                    "is_english":          bool(item.get("is_english", True)),
                    "confidence":          int(item.get("confidence", 5)),
                    "view_count_estimate": int(item.get("view_count_estimate", 3)),
                })

            # Sort chronologically so segments are in play order
            valid.sort(key=lambda x: x["start_time"])

            # Fix overlaps and tiny gaps to make segments contiguous
            for i in range(len(valid) - 1):
                cur_end  = valid[i]["end_time"]
                nxt_start = valid[i + 1]["start_time"]
                if cur_end > nxt_start:
                    # Overlap: trim this segment at the next one's start
                    valid[i]["end_time"] = nxt_start
                elif nxt_start - cur_end < 2:
                    # Tiny gap (< 2 s): extend this segment to cover it
                    valid[i]["end_time"] = nxt_start

            # Drop anything that became too short after overlap correction
            valid = [c for c in valid if (c["end_time"] - c["start_time"]) >= 3]

            total_span = sum(c["end_time"] - c["start_time"] for c in valid)
            logger.info(
                f"    Gemini Vision: {len(valid)} rank segments detected, "
                f"spanning {total_span:.0f}s of {rv_duration:.0f}s total"
            )
            return valid

        except Exception as e:
            logger.debug(f"Gemini ranking analysis failed: {e}")
            return []

    # ── Ranking-video mining ──────────────────────────────────────────────────

    def _find_ranking_videos(self, min_views: int = RANKING_MIN_VIEWS) -> list[dict]:
        """
        Search extensively for cat ranking Shorts with at least min_views.

        Only accepts actual YouTube Shorts (duration ≤ MAX_SHORTS_DURATION).
        Casts a wide net across all RANKING_SOURCE_QUERIES, then filters
        down to videos that meet the view-count AND Shorts-duration bar.
        """
        seen: set[str] = set()
        found: list[dict] = []

        queries = random.sample(RANKING_SOURCE_QUERIES, min(12, len(RANKING_SOURCE_QUERIES)))
        for q in queries:
            if len(found) >= RANKING_ANALYSE_LIMIT * 3:
                break
            logger.info(f"  Searching ranking sources: '{q[:55]}'")
            try:
                entries = self._ydl_extract_flat(f"ytsearch20:{q}", playlist_end=20)
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
                # NOTE: do NOT call _is_unwanted() here — it blocks "ranked" /
                # "ranking" which are exactly the words we need. _is_unwanted is
                # for source clips, not for ranking videos themselves.
                if not _is_ranking_video(title):
                    continue
                views = e.get("view_count") or 0
                # Only filter by views when yt-dlp actually returned a count.
                # Flat-extract often returns 0/None for view_count — skipping
                # those would silently drop valid Shorts.
                if views and views < min_views:
                    continue
                # If flat-extract gave us a duration, pre-filter obvious non-Shorts.
                # (Flat-extract often returns 0/None so we can't rely on it fully —
                # _analyze_ranking_video does the definitive gate.)
                flat_dur = e.get("duration") or 0
                if flat_dur and flat_dur > 300:   # hard pre-filter: definitely not a Short
                    continue
                seen.add(vid_id)
                found.append({
                    "id":         vid_id,
                    "url":        f"https://www.youtube.com/watch?v={vid_id}",
                    "title":      title,
                    "view_count": views,
                })

        found.sort(key=lambda x: x["view_count"], reverse=True)
        logger.info(
            f"  Found {len(found)} cat ranking Shorts with ≥{min_views:,} views"
        )
        return found

    def _analyze_ranking_video(
        self, rv: dict, seen_ids: set[str]
    ) -> list[dict]:
        """
        "Watch" a ranking video and extract the individual cat clips it used.

        We literally watch the video via Gemini Vision, identify the funny
        moments and their timestamps, then slice those timestamps directly out
        of the ranking video.  No description parsing, no YouTube searching —
        every clip is guaranteed to come from the ranking Short itself.

        Priority order:
          A. Gemini Vision    → identify timestamps; slice ranking video directly
          B. Chapter slicing  → slice ranking video at chapter boundaries
          C. Even slicing     → last resort; divide video into N even segments

        Returns a list of clip dicts, each tagged with _ranking_vid_id and
        _ranking_views so we can cross-reference across multiple ranking videos.
        """
        logger.info(
            f"  Analysing: '{rv['title'][:60]}' ({rv['view_count']:,} views)"
        )
        info = self._ydl_get_info(rv["url"])
        if not info:
            logger.info(f"    Skipping {rv['id']}: could not fetch metadata (unavailable or rate-limited)")
            return []

        rv_id       = rv["id"]
        # Use real view count from full info fetch (flat-extract often returns 0)
        rv_views    = info.get("view_count") or rv["view_count"]
        chapters    = info.get("chapters") or []
        rv_duration = info.get("duration") or 0
        clips: list[dict] = []

        # Duration gate: only clone actual Shorts (≤90 s).
        if not rv_duration:
            logger.info(f"    Skipping {rv['id']}: duration unknown — can't verify Short")
            return []
        if rv_duration > 90:
            logger.info(f"    Skipping {rv['id']}: {rv_duration:.0f}s > 90s — not a Short")
            return []
        # View gate: skip genuinely low-view videos (but allow 0 when info couldn't fetch it)
        if rv_views and rv_views < 1_000:
            logger.info(f"    Skipping {rv['id']}: {rv_views} views — too low")
            return []
        logger.info(f"    Source Short confirmed: {rv_duration:.0f}s — proceeding with analysis")

        # ── Route A: Gemini Vision → direct time-range slices ────────────────
        # Gemini WATCHES the video (multi-frame) and identifies exactly where
        # each funny cat moment starts and ends. We slice those timestamps
        # directly — no YouTube searches, no risk of pulling unrelated videos.
        api_key = os.getenv("GEMINI_API_KEY", "")

        if api_key and rv_duration >= 10:
            gemini_clips = self._gemini_analyze_ranking_video(
                rv["url"], rv_duration, chapters, api_key
            )
            # Extra gate: reject anything Gemini flagged as non-English
            gemini_clips = [gc for gc in gemini_clips if gc.get("is_english", True)]
            if gemini_clips:
                logger.info(
                    f"    Route A (Gemini Vision): "
                    f"{len(gemini_clips)} clip timestamps identified"
                )
                for gc in gemini_clips:
                    start     = gc["start_time"]
                    end       = gc["end_time"]
                    clip_id_g = f"{rv_id}_{int(start)}"
                    if clip_id_g in seen_ids or self._is_used(clip_id_g):
                        continue
                    seen_ids.add(clip_id_g)
                    # Use on-screen label (if Gemini saw it) as the sidebar title;
                    # fall back to the action description or the ranking video title.
                    raw_screen = gc.get("screen_label", "").strip()
                    clip_title = raw_screen or gc["description"][:60] or rv["title"][:30]
                    clips.append({
                        "id":                 clip_id_g,
                        "url":                rv["url"],
                        "title":              clip_title,
                        "start_time":         start,
                        "end_time":           end,  # use Gemini's detected rank transition boundary exactly
                        "platform":           "ranking_slice",
                        "view_count":         rv_views,
                        "like_count":         0,
                        "duration":           end - start,
                        "_ranking_vid_id":    rv_id,
                        "_ranking_views":     rv_views,
                        "_gemini_detected":   True,
                        "_gemini_confidence": gc["confidence"],
                        "_view_estimate":     gc["view_count_estimate"],
                    })

        target_n = getattr(self.config, 'clips_per_video', 5)

        # ── Route B: Chapter timestamp slicing ────────────────────────────────
        # Chapter markers tell us exactly where each clip starts/ends.
        # Slice the ranking video at those boundaries. No searching needed.
        if len(clips) < target_n and chapters and rv_duration >= 10:
            logger.info(f"    Route B (chapter slicing): {len(chapters)} chapters")
            last_end: float = -999.0
            for ch in chapters:
                start   = float(ch.get("start_time", 0))
                end     = float(ch.get("end_time", start + SEGMENT_TARGET_SECS))
                if rv_duration and start >= rv_duration:
                    continue
                seg_len = end - start
                if seg_len < 3 or seg_len > 300:
                    continue
                if start < last_end + 1:
                    continue
                last_end = end
                clip_id  = f"{rv_id}_{int(start)}"
                if self._is_used(clip_id) or clip_id in seen_ids:
                    continue
                seen_ids.add(clip_id)
                label = re.sub(r'^#?\d+[\.\-:\s]+', '', ch.get("title") or "").strip()
                # Skip chapter titles that are clearly non-cat content
                if label and _is_unwanted(label):
                    continue
                clips.append({
                    "id":              clip_id,
                    "url":             rv["url"],
                    "title":           label or rv["title"][:40],
                    "start_time":      start,
                    "end_time":        end,  # use chapter boundary exactly
                    "platform":        "ranking_slice",
                    "view_count":      rv_views,
                    "like_count":      0,
                    "duration":        seg_len,
                    "_ranking_vid_id": rv_id,
                    "_ranking_views":  rv_views,
                })

        # ── Route C: Even time slicing ────────────────────────────────────────
        # Fills remaining slots when Routes A/B found fewer clips than needed.
        if len(clips) < target_n and rv_duration >= 15:
            n = min(target_n, max(2, int(rv_duration / 10)))
            logger.info(f"    Route C (even slicing): {n} segments from {rv_duration:.0f}s")
            for i in range(n):
                start   = max(3.0, rv_duration * i / n)
                end     = min(rv_duration * (i + 1) / n, start + SEGMENT_TARGET_SECS)
                clip_id = f"{rv_id}_{int(start)}"
                if self._is_used(clip_id) or clip_id in seen_ids:
                    continue
                seen_ids.add(clip_id)
                # Leave title blank — _make_short_label will assign a fun
                # rank-appropriate label (e.g. "THE GOAT", "SEND HELP") based
                # on the clip's position when the video is assembled.
                clips.append({
                    "id":              clip_id,
                    "url":             rv["url"],
                    "title":           "",
                    "start_time":      start,
                    "end_time":        end,
                    "platform":        "ranking_slice",
                    "view_count":      rv_views,
                    "like_count":      0,
                    "duration":        end - start,
                    "_ranking_vid_id": rv_id,
                    "_ranking_views":  rv_views,
                })

        logger.info(f"    → {len(clips)} source clips extracted")
        return clips

    def _get_reusable_candidates(self) -> list[dict]:
        """Phase 3: previously-used clips that are under the reuse limit."""
        reusable = []
        for vid_id, data in self._used.items():
            if data.get("count", 0) < MAX_CLIP_REUSE and data.get("url"):
                reusable.append({
                    "id":         vid_id,
                    "url":        data["url"],
                    "start_time": data.get("start_time"),
                    "end_time":   data.get("end_time"),
                    "platform":   data.get("platform", "unknown"),
                    "title":      data.get("title", ""),
                    "view_count": data.get("view_count", 0),
                    "like_count": 0,
                    "duration":   0,
                    "_reuse":     True,
                })
        return reusable

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

        # ── Pick the first valid Short (≤90s, available) ─────────────────────
        for rv in ranking_vids[:RANKING_ANALYSE_LIMIT]:
            logger.info(f"Checking: '{rv['title'][:60]}' ({rv['view_count']:,} views)")
            info = self._ydl_get_info(rv["url"])
            if not info:
                logger.info(f"  Skipping {rv['id']}: unavailable or rate-limited")
                continue
            rv_duration = info.get("duration") or 0
            if not rv_duration or rv_duration > 90:
                logger.info(f"  Skipping {rv['id']}: {rv_duration:.0f}s — not a Short")
                continue
            rv_views = info.get("view_count") or rv["view_count"]
            if rv_views and rv_views < 1_000:
                logger.info(f"  Skipping {rv['id']}: {rv_views} views — too low")
                continue
            logger.info(f"  ✓ Using '{rv['title'][:55]}' ({rv_views:,} views, {rv_duration:.0f}s)")
            return [{
                "id":             rv["id"],
                "url":            rv["url"],
                "platform":       "full_ranking_short",
                "title":          rv["title"],
                "view_count":     rv_views,
                "_full_short":    True,
                "_rank_segments": [],
            }]

        logger.warning("No valid cat ranking Short found — pipeline will abort.")
        return []
