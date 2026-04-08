"""
scraper.py — Discovers viral cat clips by extracting segments from popular
YouTube compilation videos.

Strategy:
  Phase 0a — Dedicated Shorts scraper: aggressively searches for funny cat clips
              ≤20 seconds using 30+ targeted queries. These are the ideal inputs —
              the whole video is the funny moment, no slicing required.
  Phase 0b — Viral ranking sources: find cat ranking Shorts with 500k+ views,
              extract source clip IDs from descriptions and chapter titles.
  Phase 1  — Individual viral clips: 5–60s videos, with comment-timestamp peak
              detection for 20–60s clips to find the funniest window.
  Phase 2  — Compilation extraction: 1–20 min compilations sliced by chapters or
              comment timestamps.
  Phase 3  — Reuse: allow previously-used clips (up to MAX_CLIP_REUSE times).
"""
import json
import logging
import random
import re
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


_EBU_RE = re.compile(r't:\s+([\d.]+)\s+M:\s+([-\d.]+)')


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
        if not clips and chapters:
            logger.info(f"    No description links — extracting {len(chapters)} chapters")
            # Treat the ranking video itself as a source and slice by chapters
            rv_duration = info.get("duration") or 0
            for ch in chapters:
                start    = float(ch.get("start_time", 0))
                end      = float(ch.get("end_time", start + SEGMENT_TARGET_SECS))
                if rv_duration and start >= rv_duration:
                    continue
                seg_len  = end - start
                if seg_len < 3 or seg_len > 45:
                    continue
                clip_id  = f"{rv_id}_{int(start)}"
                if self._is_used(clip_id) or clip_id in seen_ids:
                    continue
                seen_ids.add(clip_id)
                label = re.sub(r'^#?\d+[\.\-:\s]+', '', ch.get("title") or "").strip()
                clips.append({
                    "id":               clip_id,
                    "url":              rv["url"],
                    "title":            label or rv["title"][:30],
                    "start_time":       start,
                    "end_time":         min(end, start + SEGMENT_TARGET_SECS),
                    "platform":         "youtube",
                    "view_count":       rv_views,
                    "like_count":       0,
                    "duration":         seg_len,
                    "_ranking_vid_id":  rv_id,
                    "_ranking_views":   rv_views,
                })

        # ── Route 3: search chapter titles to find the original source clips ──
        if not clips:
            ch_queries = []
            for ch in chapters:
                raw = (ch.get("title") or "").strip()
                cleaned = re.sub(r'^#?\d+[\.\-:\s]+', '', raw).strip()
                if len(cleaned) >= 4:
                    ch_queries.append(f"{cleaned} cat funny original")

            if not ch_queries:
                # Fallback: use the ranking video title words as a search
                words = rv["title"].lower().split()
                nouns = [w for w in words if len(w) > 3 and w not in {
                    "funniest","ranked","ranking","cats","moments","shorts","funny"
                }]
                if nouns:
                    ch_queries = [f"{' '.join(nouns[:3])} cat funny"]

            logger.info(f"    Searching {len(ch_queries)} chapter-title queries")
            for cq in ch_queries[:8]:
                try:
                    entries = self._ydl_extract_flat(f"ytsearch6:{cq}", playlist_end=6)
                    for e in entries:
                        if not e:
                            continue
                        vid_id = e.get("id", "")
                        if not vid_id or vid_id in seen_ids or self._is_used(vid_id):
                            continue
                        title = e.get("title", "")
                        if not _is_cat_video(title) or _is_unwanted(title) or not _is_english(title):
                            continue
                        duration = e.get("duration") or 0
                        if duration and duration > 60:
                            continue
                        seen_ids.add(vid_id)
                        clips.append({
                            "id":               vid_id,
                            "url":              f"https://www.youtube.com/watch?v={vid_id}",
                            "title":            title,
                            "start_time":       None,
                            "end_time":         None,
                            "platform":         "youtube",
                            "view_count":       e.get("view_count") or 0,
                            "like_count":       e.get("like_count") or 0,
                            "duration":         duration,
                            "_ranking_vid_id":  rv_id,
                            "_ranking_views":   rv_views,
                        })
                        break  # one result per chapter query
                except Exception as ex:
                    logger.debug(f"Chapter-title search failed '{cq}': {ex}")

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

        result = _dedup(selected)
        logger.info(f"Returning {len(result)} candidates from viral ranking sources")
        return result
