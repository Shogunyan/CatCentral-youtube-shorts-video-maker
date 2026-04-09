"""
scraper.py — Discovers viral cat clips from proven 1M+ view ranking Shorts.

Strategy:
  1. Find cat ranking Shorts with ≥ 1M views (falls back to 500K then 100K).
  2. Analyse up to RANKING_ANALYSE_LIMIT of them; build a cross-reference map
     so clips appearing in multiple rankings are scored higher.
  3. Take CLIPS_FROM_FIRST_RANKING clips from the #1 ranking video and
     CLIPS_FROM_SECOND_RANKING from the #2 ranking video (= 5 total).
  4. Fill remaining slots from cross-referenced clips, then reusable clips.

Source-clip detection (three routes per ranking video):
  Route 1 — Description links  → fetch exact source clip metadata from YouTube.
  Route 2 — Chapters (no links)→ slice the ranking video by chapter timestamps.
  Route 3a — Gemini Vision      → multi-frame analysis returns per-clip start/end
                                  and search queries; high-confidence clips slice
                                  the ranking video directly.
  Route 3b — Text-only fallback → chapter title search (no Gemini key).
"""
import json
import logging
import os
import random
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yt_dlp

logger = logging.getLogger(__name__)

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

MAX_CLIP_REUSE = 2

# Seconds of source clip kept around each detected peak moment.
SEGMENT_TARGET_SECS = 28

# ── Ranking-video strategy ────────────────────────────────────────────────────
# ALL clips are sourced exclusively from proven viral cat ranking Shorts.
# This guarantees every clip has already been validated as iconic/popular by
# other creators and millions of viewers.

# Only mine ranking videos above this view threshold.
RANKING_MIN_VIEWS = 1_000_000   # 1 million — truly viral only

# Clips taken from each ranking video: 3 from #1, 2 from #2 = 5 total.
CLIPS_FROM_FIRST_RANKING  = 3
CLIPS_FROM_SECOND_RANKING = 2

# Max ranking videos analysed to build the cross-reference popularity map.
RANKING_ANALYSE_LIMIT = 10

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
        "ai cat", "ai generated", "ai animation",
        "animated cat", "cartoon cat", "cgi cat", "3d cat",
        "greenscreen", "green screen",
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


class VideoScraper:
    def __init__(self, config):
        self.config = config
        self._used: dict[str, dict] = self._load_used()

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
        """Get full video metadata including chapters (no download)."""
        opts = {
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "nocheckcertificate": True,
            "skip_download": True,
            "socket_timeout": 20,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as e:
            logger.debug(f"Failed to get info for {url}: {e}")
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
            import google.generativeai as genai

            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-1.5-flash")

            with open(frame_path, "rb") as f:
                img_bytes = f.read()

            response = model.generate_content([
                {"mime_type": "image/jpeg", "data": img_bytes},
                (
                    "This is a frame from a viral funny cat video. "
                    "Describe ONLY the cat's specific action or reaction in 3-6 words, "
                    "suitable as a YouTube search query to find the original clip. "
                    "Examples: 'cat falls off counter', 'kitten scared of cucumber', "
                    "'cat yells at owner'. Reply with just the phrase, nothing else."
                ),
            ])
            description = response.text.strip().lower()
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
        Send multiple frames from a ranking video to Gemini in ONE call.

        Gemini identifies each individual cat clip and returns structured data:
            start_time, end_time          — precise timestamps in the ranking video
            description                   — what the cat is doing (for logging)
            search_query                  — YouTube search to find the original clip
            is_real_cat / is_animated     — content filters
            view_count_estimate (1-5)     — how viral Gemini thinks this clip is
            confidence (1-10)             — timestamp accuracy confidence

        High-confidence clips (≥6) are sliced directly; lower confidence clips
        use search_query to find the original standalone video.
        """
        try:
            import json as _json
            import google.generativeai as genai

            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-1.5-flash")

            n_frames = min(10, max(4, int(rv_duration / 8)))
            logger.info(
                f"    Gemini: extracting {n_frames} frames from "
                f"{rv_duration:.0f}s ranking video…"
            )
            frame_data = self._extract_frames_for_analysis(rv_url, rv_duration, n_frames)
            if not frame_data:
                logger.debug("    Gemini: no frames extracted")
                return []

            # Give Gemini the chapter marker context so it can correlate clip
            # boundaries to exact timestamps more accurately.
            _CH_NUM_RE = re.compile(r"^#?\d+[\.\-:\s]+")
            chapter_hint = ""
            if chapters:
                ch_str = "; ".join(
                    f"#{i+1} '{_CH_NUM_RE.sub('', ch.get('title', '').strip())}'"
                    f" @{ch.get('start_time', 0):.0f}s"
                    for i, ch in enumerate(chapters[:12])
                )
                chapter_hint = f"\nChapter markers (use these to anchor boundaries): {ch_str}"

            prompt_parts: list = [
                f"You are analyzing a YouTube cat ranking/countdown video ({rv_duration:.0f}s long)."
                f"{chapter_hint}\n\n"
                f"I am providing {len(frame_data)} frames at these timestamps: "
                f"{', '.join(f'{t:.1f}s' for t, _ in frame_data)}\n\n"
                "Identify EACH individual cat clip shown in this ranking video.\n"
                "For EACH clip return a JSON object with these exact fields:\n"
                "  start_time: integer seconds from video start (clip begins here)\n"
                "  end_time: integer seconds from video start (clip ends here)\n"
                "  description: 10-20 word description of the cat's specific action/reaction\n"
                "  search_query: 4-8 word YouTube search to find the ORIGINAL standalone clip\n"
                "  is_real_cat: true only if a LIVE REAL cat (not animated, CGI, or Zoom/camera filter)\n"
                "  is_animated: true if cartoon, animation, or CGI\n"
                "  is_english: true if any on-screen text is English, or no text is visible\n"
                "  is_cat_primary_subject: true only if the cat is the MAIN focus and occupies the majority of screen attention (NOT a human editing video software, NOT a reaction clip, NOT the cat barely visible in background)\n"
                "  is_screen_recording: true if this frame shows video editing software, a desktop screen recording, someone editing audio/video, or any meta-content about video creation\n"
                "  view_count_estimate: 1=unknown 2=low 3=medium 4=high 5=extremely viral\n"
                "  confidence: 1-10 confidence in the timestamp accuracy\n\n"
                "Rules:\n"
                "- EXCLUDE any clip where is_real_cat is false or is_animated is true\n"
                "- EXCLUDE any clip where is_cat_primary_subject is false\n"
                "- EXCLUDE any clip where is_screen_recording is true\n"
                "- EXCLUDE clips that appear to be a human using a cat filter (Zoom, Snapchat, etc.)\n"
                "- Timestamps must be within 0 and " + str(int(rv_duration)) + "s\n"
                "- Each clip must be at least 3 seconds long\n"
                "Reply ONLY with a valid JSON array — no markdown, no extra text.\n"
                'Example: [{"start_time":0,"end_time":18,'
                '"description":"orange tabby slides off leather couch in slow motion",'
                '"search_query":"cat slides off couch funny original",'
                '"is_real_cat":true,"is_animated":false,"is_english":true,'
                '"is_cat_primary_subject":true,"is_screen_recording":false,'
                '"view_count_estimate":4,"confidence":8}]\n',
            ]
            for ts, frame_bytes in frame_data:
                prompt_parts.append(f"\n[Frame at {ts:.1f}s]:")
                prompt_parts.append({"mime_type": "image/jpeg", "data": frame_bytes})

            response = model.generate_content(prompt_parts)
            raw = response.text.strip()
            # Strip markdown fences then find the first JSON array — Gemini
            # sometimes prefixes the array with a sentence of prose.
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
                # Hard content filter — drop animated, non-real-cat, and meta clips
                if item.get("is_animated", False):
                    continue
                if not item.get("is_real_cat", True):
                    continue
                if not item.get("is_cat_primary_subject", True):
                    continue
                if item.get("is_screen_recording", False):
                    continue
                start = float(item.get("start_time", 0))
                end   = float(item.get("end_time", start + SEGMENT_TARGET_SECS))
                if end <= start or (end - start) < 3:
                    continue
                # Clamp to video bounds
                start = max(0.0, min(start, rv_duration))
                end   = min(end, rv_duration)
                valid.append({
                    "start_time":             start,
                    "end_time":               end,
                    "description":            str(item.get("description", ""))[:120],
                    "search_query":           str(item.get("search_query", ""))[:80],
                    "is_real_cat":            bool(item.get("is_real_cat", True)),
                    "is_animated":            bool(item.get("is_animated", False)),
                    "is_english":             bool(item.get("is_english", True)),
                    "is_cat_primary_subject": bool(item.get("is_cat_primary_subject", True)),
                    "is_screen_recording":    bool(item.get("is_screen_recording", False)),
                    "view_count_estimate":    int(item.get("view_count_estimate", 1)),
                    "confidence":             int(item.get("confidence", 5)),
                })

            # Best clips first: highest confidence, then most viral estimate
            valid.sort(key=lambda x: (-x["confidence"], -x["view_count_estimate"]))
            logger.info(
                f"    Gemini Vision: identified {len(valid)} valid real-cat clips"
            )
            return valid

        except Exception as e:
            logger.debug(f"Gemini ranking analysis failed: {e}")
            return []

    # ── Ranking-video mining ──────────────────────────────────────────────────

    def _find_ranking_videos(self, min_views: int = RANKING_MIN_VIEWS) -> list[dict]:
        """
        Search extensively for cat ranking Shorts with at least min_views.

        Casts a wide net across all RANKING_SOURCE_QUERIES, then filters
        down to videos that meet the view-count bar.  If flat-extract doesn't
        return a view count, we skip that entry (avoids slow full-info fetches
        for every result).
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
                views = e.get("view_count") or 0
                if views < min_views:
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
            f"  Found {len(found)} ranking videos with ≥{min_views:,} views"
        )
        return found

    def _analyze_ranking_video(
        self, rv: dict, seen_ids: set[str]
    ) -> list[dict]:
        """
        "Watch" a ranking video — parse its description and chapters to
        extract the EXACT source clips it used.

        Priority order:
          1. Description links  → directly fetch each source clip's info
          2. Chapters (no links)→ extract segments from the ranking video itself
          3. Chapter titles      → search YouTube to find the matching source clip

        Returns a list of clip dicts, each tagged with _ranking_vid_id and
        _ranking_views so we can cross-reference across multiple ranking videos.
        """
        logger.info(
            f"  Analysing: '{rv['title'][:60]}' ({rv['view_count']:,} views)"
        )
        info = self._ydl_get_info(rv["url"])
        if not info:
            return []

        rv_id     = rv["id"]
        rv_views  = rv["view_count"]
        desc      = info.get("description") or ""
        chapters  = info.get("chapters") or []
        clips: list[dict] = []

        # ── Route 1: description contains source YouTube links ────────────────
        src_ids = [
            sid for sid in _YT_ID_RE.findall(desc)
            if sid != rv_id and sid not in seen_ids and not self._is_used(sid)
        ]
        logger.info(f"    {len(src_ids)} source ID(s) found in description")

        for src_id in src_ids:
            seen_ids.add(src_id)
            src_url  = f"https://www.youtube.com/watch?v={src_id}"
            src_info = self._ydl_get_info(src_url)
            if not src_info:
                continue
            src_title = src_info.get("title") or ""
            if not _is_cat_video(src_title) or _is_unwanted(src_title) or not _is_english(src_title):
                continue
            duration = src_info.get("duration") or 0
            views    = src_info.get("view_count") or 0

            if duration and duration > 60:
                # Long source — slice best segments out of it
                segs = self._clips_from_compilation({
                    "id": src_id, "url": src_url, "title": src_title,
                    "duration": duration, "view_count": views,
                })
                for s in segs:
                    s["_ranking_vid_id"]  = rv_id
                    s["_ranking_views"]   = rv_views
                clips.extend(segs)
                logger.info(
                    f"    Source {src_id}: {len(segs)} segments from {duration:.0f}s"
                )
            else:
                clips.append({
                    "id":               src_id,
                    "url":              src_url,
                    "title":            src_title,
                    "start_time":       None,
                    "end_time":         None,
                    "platform":         "youtube",
                    "view_count":       views,
                    "like_count":       src_info.get("like_count") or 0,
                    "duration":         duration,
                    "_ranking_vid_id":  rv_id,
                    "_ranking_views":   rv_views,
                })

        # ── Route 2: chapters present but no description links ────────────────
        # Skip entirely when Gemini is available — Route 3a handles this better
        # by searching for ORIGINAL standalone videos for each clip.
        _has_gemini = bool(os.getenv("GEMINI_API_KEY", ""))
        if not clips and chapters and not _has_gemini:
            logger.info(
                f"    No description links — searching originals for "
                f"{len(chapters)} chapters (no Gemini key)"
            )
            rv_duration = info.get("duration") or 0
            last_end: float = -999.0
            for ch in chapters:
                start    = float(ch.get("start_time", 0))
                end      = float(ch.get("end_time", start + SEGMENT_TARGET_SECS))
                if rv_duration and start >= rv_duration:
                    continue
                seg_len  = end - start
                if seg_len < 3 or seg_len > 45:
                    continue
                # Require at least 15s gap from previous accepted clip
                if start < last_end + 15:
                    continue
                last_end = end

                raw_label = re.sub(
                    r'^#?\d+[\.\-:\s]+', '', ch.get("title") or ""
                ).strip()

                # Step 1: Search for original standalone video using chapter title
                found_original = False
                if raw_label:
                    for cq in [
                        f"{raw_label} cat original",
                        f"{raw_label} funny cat",
                        raw_label,
                    ]:
                        if found_original:
                            break
                        try:
                            entries = self._ydl_extract_flat(
                                f"ytsearch5:{cq}", playlist_end=5
                            )
                            for e in entries:
                                if not e:
                                    continue
                                vid_id = e.get("id", "")
                                if (not vid_id or vid_id in seen_ids
                                        or self._is_used(vid_id)):
                                    continue
                                title = e.get("title", "")
                                if (not _is_cat_video(title)
                                        or _is_unwanted(title)
                                        or not _is_english(title)):
                                    continue
                                dur = e.get("duration") or 0
                                if dur and dur > 90:
                                    continue
                                views = e.get("view_count") or 0
                                if views > 0 and views < 100_000:
                                    continue
                                seen_ids.add(vid_id)
                                clips.append({
                                    "id":              vid_id,
                                    "url":             f"https://www.youtube.com/watch?v={vid_id}",
                                    "title":           title,
                                    "start_time":      None,
                                    "end_time":        None,
                                    "platform":        "youtube",
                                    "view_count":      views,
                                    "like_count":      e.get("like_count") or 0,
                                    "duration":        dur,
                                    "_ranking_vid_id": rv_id,
                                    "_ranking_views":  rv_views,
                                })
                                found_original = True
                                logger.debug(
                                    f"      Route2 chapter '{raw_label}'"
                                    f" → original found: {vid_id}"
                                )
                                break
                        except Exception as ex:
                            logger.debug(
                                f"Route 2 original search failed '{cq}': {ex}"
                            )

                # Step 2: Fall back to ranking slice if no original found
                if not found_original:
                    clip_id = f"{rv_id}_{int(start)}"
                    if self._is_used(clip_id) or clip_id in seen_ids:
                        continue
                    seen_ids.add(clip_id)
                    clips.append({
                        "id":               clip_id,
                        "url":              rv["url"],
                        "title":            raw_label or rv["title"][:30],
                        "start_time":       start,
                        "end_time":         min(end, start + SEGMENT_TARGET_SECS),
                        "platform":         "ranking_slice",
                        "view_count":       rv_views,
                        "like_count":       0,
                        "duration":         seg_len,
                        "_ranking_vid_id":  rv_id,
                        "_ranking_views":   rv_views,
                    })
                    logger.debug(
                        f"      Route2 chapter '{raw_label}'"
                        f" → ranking slice fallback @{start:.0f}s"
                    )

        # ── Route 3: Gemini Vision analysis + targeted search for originals ────
        if not clips:
            api_key     = os.getenv("GEMINI_API_KEY", "")
            rv_duration = info.get("duration") or 0
            gemini_clips: list[dict] = []

            # ── Route 3a: multi-frame Gemini analysis (API key required) ────────
            if api_key and rv_duration >= 10:
                gemini_clips = self._gemini_analyze_ranking_video(
                    rv["url"], rv_duration, chapters, api_key
                )

            if gemini_clips:
                logger.info(
                    f"    Route 3a (Gemini Vision): "
                    f"processing {len(gemini_clips)} identified clips"
                )
                for gc in gemini_clips:
                    start      = gc["start_time"]
                    end        = gc["end_time"]
                    confidence = gc["confidence"]
                    sq         = gc["search_query"]
                    clip_id_g  = f"{rv_id}_{int(start)}"

                    # Step 1: Always try to find the ORIGINAL standalone clip
                    # (original clips look better — no ranking overlay burned in)
                    found_original = False
                    if sq:
                        for cq in [f"{sq} original", f"{sq} funny cat", sq]:
                            if found_original:
                                break
                            try:
                                entries = self._ydl_extract_flat(
                                    f"ytsearch5:{cq}", playlist_end=5
                                )
                                for e in entries:
                                    if not e:
                                        continue
                                    vid_id = e.get("id", "")
                                    if (not vid_id or vid_id in seen_ids
                                            or self._is_used(vid_id)):
                                        continue
                                    title = e.get("title", "")
                                    if (not _is_cat_video(title)
                                            or _is_unwanted(title)
                                            or not _is_english(title)):
                                        continue
                                    dur = e.get("duration") or 0
                                    if dur and dur > 90:
                                        continue
                                    views = e.get("view_count") or 0
                                    # Clips in viral rankings must have real traction
                                    if views > 0 and views < 100_000:
                                        continue
                                    seen_ids.add(vid_id)
                                    clips.append({
                                        "id":               vid_id,
                                        "url":              f"https://www.youtube.com/watch?v={vid_id}",
                                        "title":            title,
                                        "start_time":       None,
                                        "end_time":         None,
                                        "platform":         "youtube",
                                        "view_count":       views,
                                        "like_count":       e.get("like_count") or 0,
                                        "duration":         dur,
                                        "_ranking_vid_id":  rv_id,
                                        "_ranking_views":   rv_views,
                                        "_visual_matched":  True,
                                        "_gemini_detected": True,
                                    })
                                    found_original = True
                                    logger.debug(
                                        f"      @{start:.0f}s  sq='{sq}'  "
                                        f"→ original found: {vid_id}"
                                    )
                                    break
                            except Exception as ex:
                                logger.debug(f"Route 3a search failed '{cq}': {ex}")

                    # Step 2: Fall back to direct slice only if no original found
                    # and Gemini is confident enough about the timestamp.
                    if not found_original and confidence >= 6:
                        if clip_id_g in seen_ids or self._is_used(clip_id_g):
                            continue
                        seen_ids.add(clip_id_g)
                        clips.append({
                            "id":                 clip_id_g,
                            "url":                rv["url"],
                            "title":              gc["description"][:60] or rv["title"][:30],
                            "start_time":         start,
                            "end_time":           min(end, start + SEGMENT_TARGET_SECS),
                            "platform":           "ranking_slice",
                            "view_count":         rv_views,
                            "like_count":         0,
                            "duration":           end - start,
                            "_ranking_vid_id":    rv_id,
                            "_ranking_views":     rv_views,
                            "_gemini_detected":   True,
                            "_gemini_confidence": confidence,
                            "_view_estimate":     gc["view_count_estimate"],
                        })
                        logger.debug(
                            f"      @{start:.0f}s–{end:.0f}s  "
                            f"conf={confidence}  viral={gc['view_count_estimate']}  "
                            f"→ ranking slice fallback"
                        )

            else:
                # ── Route 3b: text-only fallback (no API key / Gemini failed) ─
                segments: list[tuple[str, float]] = []
                if chapters:
                    for ch in chapters:
                        raw = (ch.get("title") or "").strip()
                        cleaned = re.sub(r'^#?\d+[\.\-:\s]+', '', raw).strip()
                        mid = (
                            float(ch.get("start_time", 0)) +
                            float(ch.get("end_time",
                                         float(ch.get("start_time", 0)) + 12))
                        ) / 2
                        segments.append((cleaned, mid))
                elif rv_duration >= 10:
                    n = min(5, max(2, int(rv_duration / 12)))
                    for i in range(n):
                        mid = rv_duration * (i + 0.5) / n
                        segments.append(("", mid))

                if not segments:
                    words = rv["title"].lower().split()
                    nouns = [
                        w for w in words if len(w) > 3
                        and w not in {
                            "funniest", "ranked", "ranking", "cats",
                            "moments", "shorts", "funny",
                        }
                    ]
                    if nouns:
                        segments = [(f"{' '.join(nouns[:3])} cat funny", 0.0)]

                logger.info(f"    Route 3b (text-only): {len(segments)} segments")

                for ch_title, _ in segments[:8]:
                    if not ch_title:
                        continue
                    try:
                        entries = self._ydl_extract_flat(
                            f"ytsearch8:{ch_title} cat funny original",
                            playlist_end=8,
                        )
                        for e in entries:
                            if not e:
                                continue
                            vid_id = e.get("id", "")
                            if (not vid_id or vid_id in seen_ids
                                    or self._is_used(vid_id)):
                                continue
                            title = e.get("title", "")
                            if (not _is_cat_video(title) or _is_unwanted(title)
                                    or not _is_english(title)):
                                continue
                            dur = e.get("duration") or 0
                            if dur and dur > 90:
                                continue
                            views = e.get("view_count") or 0
                            if views > 0 and views < 100_000:
                                continue
                            seen_ids.add(vid_id)
                            clips.append({
                                "id":               vid_id,
                                "url":              f"https://www.youtube.com/watch?v={vid_id}",
                                "title":            title,
                                "start_time":       None,
                                "end_time":         None,
                                "platform":         "youtube",
                                "view_count":       views,
                                "like_count":       e.get("like_count") or 0,
                                "duration":         dur,
                                "_ranking_vid_id":  rv_id,
                                "_ranking_views":   rv_views,
                                "_visual_matched":  False,
                            })
                            break
                    except Exception as ex:
                        logger.debug(f"Route 3b search failed '{ch_title}': {ex}")

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

    def get_candidates(
        self,
        want: int = 25,
        yt_queries: list[str] | None = None,
        tt_hashtags: list[str] | None = None,
    ) -> list[dict]:
        """
        Return exactly `want` clip candidates sourced from proven viral
        cat ranking Shorts (1M+ views).

        Strategy:
          1. Find ranking videos with 1M+ views (fallback to 500K / 100K).
          2. Analyse up to RANKING_ANALYSE_LIMIT of them; build a cross-reference
             map so clips used by multiple rankings are ranked highest.
          3. Take CLIPS_FROM_FIRST_RANKING from the #1 ranking video and
             CLIPS_FROM_SECOND_RANKING from the #2 ranking video.
          4. Fill any remaining slots from cross-referenced clips, then
             reusable clips, then (last resort) lower-view-count rankings.
        """
        def _dedup(videos: list[dict]) -> list[dict]:
            seen: set[str] = set()
            out: list[dict] = []
            for v in videos:
                if v.get("id") and v["id"] not in seen:
                    seen.add(v["id"])
                    out.append(v)
            return out

        seen_ids: set[str] = set()

        # ── Find ranking videos ───────────────────────────────────────────────
        logger.info("Searching for cat ranking Shorts with 1M+ views…")
        ranking_vids = self._find_ranking_videos(min_views=1_000_000)

        if len(ranking_vids) < 2:
            logger.info("Not enough 1M+ rankings — widening to 500K+…")
            ranking_vids = self._find_ranking_videos(min_views=500_000)
        if len(ranking_vids) < 2:
            logger.info("Still short — widening to 100K+…")
            ranking_vids = self._find_ranking_videos(min_views=100_000)

        if not ranking_vids:
            logger.warning("Could not find any cat ranking videos — returning empty")
            return []

        # ── Analyse ranking videos; build popularity cross-reference map ─────
        logger.info(
            f"Analysing top {min(RANKING_ANALYSE_LIMIT, len(ranking_vids))} "
            f"ranking videos…"
        )
        # Map: source_clip_id → {clip_data, cross_count}
        cross_map: dict[str, dict] = {}
        # List of (ranking_vid, [source_clips]) in view-count order
        analysed: list[tuple[dict, list[dict]]] = []

        for rv in ranking_vids[:RANKING_ANALYSE_LIMIT]:
            seen_ids.add(rv["id"])
            clips = self._analyze_ranking_video(rv, seen_ids)
            fresh = [c for c in clips if self._use_count(c["id"]) == 0]
            if fresh:
                analysed.append((rv, fresh))
            for c in fresh:
                sid = c["id"]
                if sid in cross_map:
                    cross_map[sid]["_cross_count"] += 1
                else:
                    cross_map[sid] = {**c, "_cross_count": 1}

        if not analysed:
            logger.warning("No source clips found in any ranking video")
            reusable = self._get_reusable_candidates()
            return reusable[:want]

        # ── Pick clips: 3 from #1 ranking video, 2 from #2 ───────────────────
        def _rank_key(c: dict) -> tuple:
            return (
                -cross_map.get(c["id"], {}).get("_cross_count", 0),
                -c.get("view_count", 0),
            )

        selected: list[dict] = []

        if len(analysed) >= 1:
            rv1, clips1 = analysed[0]
            clips1_sorted = sorted(clips1, key=_rank_key)
            take = clips1_sorted[:CLIPS_FROM_FIRST_RANKING]
            selected.extend(take)
            logger.info(
                f"  Ranking #1 '{rv1['title'][:50]}' "
                f"({rv1['view_count']:,} views) → {len(take)} clips"
            )

        if len(analysed) >= 2:
            rv2, clips2 = analysed[1]
            used = {c["id"] for c in selected}
            clips2_sorted = sorted(
                [c for c in clips2 if c["id"] not in used], key=_rank_key
            )
            take = clips2_sorted[:CLIPS_FROM_SECOND_RANKING]
            selected.extend(take)
            logger.info(
                f"  Ranking #2 '{rv2['title'][:50]}' "
                f"({rv2['view_count']:,} views) → {len(take)} clips"
            )

        # ── Fill remaining slots from cross-referenced pool ───────────────────
        if len(selected) < want:
            used = {c["id"] for c in selected}
            # All cross-referenced clips, sorted by how many rankings used them
            xref_pool = sorted(
                [c for c in cross_map.values() if c["id"] not in used],
                key=lambda c: (-c.get("_cross_count", 0), -c.get("view_count", 0)),
            )
            for c in xref_pool:
                if len(selected) >= want:
                    break
                selected.append(c)
            logger.info(
                f"  Cross-reference fill: now have {len(selected)}/{want} clips"
            )

        # ── Last resort: reusable clips ───────────────────────────────────────
        if len(selected) < want:
            used = {c["id"] for c in selected}
            reusable = [
                c for c in self._get_reusable_candidates()
                if c["id"] not in used
            ]
            selected.extend(reusable[: want - len(selected)])
            logger.info(f"  Reuse fill: now have {len(selected)}/{want} clips")

        # ── URL dedup: one original standalone video per clip slot ─────────────
        # Prevents the same YouTube video from filling multiple rank slots.
        # Segment clips (start_time != None) are exempt — they're different
        # timestamped moments from a ranking compilation and are already diverse.
        url_seen: set[str] = set()
        url_clean: list[dict] = []
        for c in selected:
            url = c.get("url", "")
            if c.get("start_time") is not None:
                url_clean.append(c)   # segment from ranking video — allow multiple
            elif url and url in url_seen:
                logger.debug(
                    f"  URL-dedup: skipping duplicate standalone {c.get('id')} ({url[-40:]})"
                )
            else:
                url_seen.add(url)
                url_clean.append(c)
        selected = url_clean

        result = _dedup(selected)
        logger.info(f"Returning {len(result)} candidates from viral ranking sources")
        return result
