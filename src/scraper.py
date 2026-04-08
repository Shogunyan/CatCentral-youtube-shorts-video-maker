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

# Max seconds per compilation segment.  28s gives enough runway to show the
# setup AND the punchline without cramming two separate moments together.
SEGMENT_TARGET_SECS = 28

# ── Dedicated Shorts queries: targets ≤20s funny cat clips ───────────────────
# These are highly specific searches that reliably surface proper short-form
# cat clips where the whole video IS the funny moment.
SHORT_CAT_QUERIES = [
    # Reaction / surprise moments
    "cat jumpscare reaction original shorts",
    "cat scared suddenly funny shorts",
    "cat surprised face shorts",
    "cat shocked by owner shorts",
    "cat attacks feet funny shorts",
    "cat slaps dog funny shorts",
    "cat hissing at mirror shorts",
    "cat bites owner funny shorts",
    # Behaviour / physics moments
    "cat zoomies 3am shorts",
    "cat falls off shelf funny",
    "cat fails jump funny shorts",
    "cat knocks glass off table shorts",
    "cat derp face funny shorts",
    "kitten discovers stairs shorts",
    "cat loaf falls over shorts",
    "cat refuses to move funny",
    "cat sploots funny shorts",
    # Sound moments
    "cat yowling loudly funny short",
    "cat makes weird noise short clip",
    "cat chatters at window short",
    "cat chirps at bird short clip",
    "cat screams funny short",
    # Expression / stare moments
    "cat staring into void shorts",
    "cat judges owner shorts funny",
    "cat slow blink funny shorts",
    "cat unimpressed face shorts",
    "cat caught red handed funny short",
    # Classic viral cat shorts
    "viral cat moment 2024 shorts",
    "viral cat moment 2025 shorts",
    "funny cat shorts 2024",
    "funny cat shorts 2025",
    "cats being weird shorts compilation",
]

# ── Primary: individual short viral cat clips ─────────────────────────────────
# Every query explicitly contains "cat" so YouTube returns cat content.
# Target: short individual videos (5–60s) where the whole clip = the moment.
VIRAL_CAT_QUERIES = [
    # YouTube Shorts cat clips (most reliable)
    "funny cat shorts",
    "cat being funny short",
    "cat scared funny short",
    "cat attack funny shorts",
    "cat zoomies shorts",
    "cat yelling funny shorts",
    "kitten funny shorts",
    "cats being weird shorts",
    "cat reaction funny shorts",
    "cat fails funny shorts",
    # Classic viral cat moments
    "funny cat video 2019",
    "funny cat video 2020",
    "viral cat video original",
    "cat makes weird noise funny",
    "cat knocking things off table",
    "cat vs mirror funny",
    "cat jumps scare funny",
    "cat refuses to move funny",
    "cat falls off counter funny",
    "cat hissing funny",
    "cat chirping funny",
    "cat obsessed funny",
    "cats going crazy funny",
    "cat caught doing something funny",
]

# ── Secondary: TikTok cat compilations on YouTube ────────────────────────────
# Well-curated collections of TikTok cat clips; chapters give exact boundaries.
COMPILATION_QUERIES = [
    "funny cat tiktok compilation 2024",
    "funny cat tiktok compilation 2025",
    "best cat tiktok clips compilation",
    "cats tiktok funny compilation",
    "viral cat tiktok moments compilation",
    "funniest cat tiktok videos compilation",
    "cat tiktok compilation no commentary",
    "daily dose of internet cat videos",
    "cat fails tiktok compilation",
]

# ── Phase 0: viral cat ranking videos as clip sources ─────────────────────────
# Popular "Top 5 / Ranked" cat Shorts — we mine their descriptions and chapter
# titles to find the actual source clips inside them.
RANKING_SOURCE_QUERIES = [
    "top 10 funniest cat moments ranked shorts",
    "best cat videos ranked funny",
    "cat ranking countdown funny shorts",
    "funniest cats ranked youtube shorts",
    "top cat moments compilation ranked",
    "cat ranking #1 funny shorts",
    "cats ranked worst to best funny moments",
]

# Minimum views a ranking video must have before we mine it.
RANKING_MIN_VIEWS = 500_000

# How many ranking videos to analyse per pipeline run.
RANKING_ANALYSE_COUNT = 3

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

    def _search_compilations(self, query: str, max_results: int = 6) -> list[dict]:
        """Search for compilation/highlight videos (1–20 minutes long)."""
        entries = self._ydl_extract_flat(
            f"ytsearch{max_results}:{query}", playlist_end=max_results
        )
        results = []
        for e in entries:
            if not e:
                continue
            vid_id = e.get("id", "")
            if not vid_id:
                continue
            title = e.get("title", "")
            if not _is_cat_video(title):
                logger.debug(f"Skipping non-cat compilation: {title!r}")
                continue
            if _is_unwanted(title):
                continue
            duration = e.get("duration") or 0
            # Target: 1–20 minute compilation videos
            if duration and not (60 <= duration <= 1200):
                continue
            results.append({
                "id":         vid_id,
                "url":        f"https://www.youtube.com/watch?v={vid_id}",
                "title":      title,
                "duration":   duration,
                "view_count": e.get("view_count") or 0,
            })
        return sorted(results, key=lambda x: x["view_count"], reverse=True)

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

    def _scrape_compilations(
        self,
        queries: list[str] | None = None,
        want: int = 25,
    ) -> list[dict]:
        """Run compilation-based scraping across multiple queries."""
        all_clips: list[dict] = []
        q_list = queries or random.sample(
            COMPILATION_QUERIES, min(5, len(COMPILATION_QUERIES))
        )
        for q in q_list:
            if len(all_clips) >= want:
                break
            logger.info(f"Searching compilations: '{q[:50]}'")
            try:
                compilations = self._search_compilations(q, max_results=5)
                logger.info(f"  Found {len(compilations)} compilations")
                for comp in compilations[:3]:
                    if len(all_clips) >= want:
                        break
                    clips = self._clips_from_compilation(comp)
                    logger.info(
                        f"  Extracted {len(clips)} clips from "
                        f"'{comp['title'][:40]}'"
                    )
                    all_clips.extend(clips)
            except Exception as e:
                logger.warning(f"Compilation query failed '{q}': {e}")
        return all_clips

    def _scrape_individual_fallback(
        self,
        queries: list[str] | None = None,
    ) -> list[dict]:
        """
        Search for individual short viral cat clips.

        These are complete videos (5–60s) where the whole clip is the funny
        moment — no slicing needed and no risk of grabbing two cats in one slot.

        Comment-timestamp peak detection is deferred to post-processing on the
        top 8 mid-length candidates only, to avoid 50+ sequential HTTP fetches
        that would stall the pipeline for many minutes.
        """
        all_videos: list[dict] = []
        q_list = queries or random.sample(VIRAL_CAT_QUERIES, min(8, len(VIRAL_CAT_QUERIES)))
        for q in q_list:
            try:
                entries = self._ydl_extract_flat(f"ytsearch20:{q}", playlist_end=20)
                for e in entries:
                    if not e:
                        continue
                    vid_id = e.get("id", "")
                    if not vid_id or self._is_used(vid_id):
                        continue
                    duration = e.get("duration") or 0
                    # Keep short individual clips only (whole video = the moment)
                    if duration and duration > 60:
                        continue
                    title = e.get("title", "")
                    # Hard reject: must be an actual cat video
                    if not _is_cat_video(title):
                        logger.debug(f"Rejected (not a cat): {title!r}")
                        continue
                    if _is_unwanted(title):
                        continue
                    all_videos.append({
                        "id":         vid_id,
                        "url":        f"https://www.youtube.com/watch?v={vid_id}",
                        "title":      title,
                        "start_time": None,
                        "end_time":   None,
                        "platform":   "youtube",
                        "view_count": e.get("view_count") or 0,
                        "like_count": e.get("like_count") or 0,
                        "duration":   duration,
                    })
            except Exception as e:
                logger.warning(f"Individual clip query failed '{q}': {e}")

        # Sort by views so we post-process the best candidates first
        all_videos.sort(key=lambda x: x["view_count"], reverse=True)

        # Post-process: use comment timestamps for the top mid-length clips only.
        # Limit to 8 fetches max to keep total extra latency under ~60s.
        mid_length = [v for v in all_videos if v["duration"] and 20 <= v["duration"] <= 60]
        logger.info(
            f"  Comment-timestamp peak detection on top "
            f"{min(8, len(mid_length))}/{len(mid_length)} mid-length clips…"
        )
        for clip_entry in mid_length[:8]:
            duration = clip_entry["duration"]
            ts_list = self._get_comment_timestamps(clip_entry["url"], duration)
            if ts_list:
                # Start 3s BEFORE the crowd-voted peak so the setup is visible
                best_start = max(0.0, ts_list[0] - 3.0)
                best_end = min(best_start + SEGMENT_TARGET_SECS, duration - 1)
                if best_end > best_start + 4:
                    clip_entry["start_time"] = best_start
                    clip_entry["end_time"]   = best_end
                    clip_entry["id"] = f"{clip_entry['id']}_{int(best_start)}"
                    logger.info(
                        f"  Peak moment {clip_entry['id']}: "
                        f"{best_start:.1f}s–{best_end:.1f}s "
                        f"({len(ts_list)} comment votes)"
                    )

        return all_videos

    # ── Dedicated Shorts scraper (≤20s clips) ────────────────────────────────

    def _scrape_cat_shorts(self, want: int = 25) -> list[dict]:
        """
        Find proper short-form funny cat clips — ideally ≤20 seconds.

        These are the gold-standard inputs: the entire video is the funny
        moment, no slicing needed.  YouTube Shorts sometimes report duration=0
        in flat-extract, so we verify ambiguous entries with a full info fetch.
        """
        found: list[dict] = []
        seen: set[str] = set()

        queries = random.sample(SHORT_CAT_QUERIES, min(14, len(SHORT_CAT_QUERIES)))

        for q in queries:
            if len(found) >= want:
                break
            try:
                entries = self._ydl_extract_flat(f"ytsearch25:{q}", playlist_end=25)
                for e in entries:
                    if not e:
                        continue
                    vid_id = e.get("id", "")
                    if not vid_id or vid_id in seen or self._is_used(vid_id):
                        continue
                    title = e.get("title", "")
                    if not _is_cat_video(title):
                        continue
                    if _is_unwanted(title):
                        continue
                    duration = e.get("duration") or 0
                    # Hard reject anything confirmed longer than 20s
                    if duration and duration > 20:
                        continue
                    seen.add(vid_id)
                    # Unknown duration (many Shorts report 0) — verify with full fetch
                    view_count = e.get("view_count") or 0
                    like_count = e.get("like_count") or 0
                    if not duration:
                        info = self._ydl_get_info(
                            f"https://www.youtube.com/watch?v={vid_id}"
                        )
                        if not info:
                            continue
                        duration = info.get("duration") or 0
                        if duration > 20:
                            continue
                        title = info.get("title") or title
                        if not _is_cat_video(title):
                            continue
                        # Prefer the richer view/like counts from the full fetch
                        view_count = info.get("view_count") or view_count
                        like_count = info.get("like_count") or like_count

                    found.append({
                        "id":         vid_id,
                        "url":        f"https://www.youtube.com/watch?v={vid_id}",
                        "title":      title,
                        "start_time": None,
                        "end_time":   None,
                        "platform":   "youtube",
                        "view_count": view_count,
                        "like_count": like_count,
                        "duration":   duration,
                        "_shorts":    True,
                    })
            except Exception as ex:
                logger.warning(f"Cat Shorts query failed '{q}': {ex}")

        found.sort(key=lambda x: x.get("view_count", 0), reverse=True)
        logger.info(f"Cat Shorts scraper: {len(found)} clips ≤20s found")
        return found

    # ── Phase 0: mine viral ranking videos for source clips ──────────────────

    def _scrape_viral_ranking_sources(self, want: int = 20) -> list[dict]:
        """
        Phase 0 — High-priority candidates from popular cat ranking Shorts.

        Algorithm:
          1. Search for cat ranking videos with 500k+ views.
          2. Analyse at least RANKING_ANALYSE_COUNT of them.
          3. Extract source video IDs from their descriptions.
          4. For each source video:
             - If short (≤60s): use as an individual clip directly.
             - If longer: extract segments via chapters / comment timestamps.
          5. Build search queries from chapter titles and run them too.
        """
        phase0: list[dict] = []
        seen_ids: set[str] = set()
        ranking_vids: list[dict] = []

        # Step 1: find popular ranking videos
        queries = random.sample(RANKING_SOURCE_QUERIES, min(4, len(RANKING_SOURCE_QUERIES)))
        for q in queries:
            if len(ranking_vids) >= RANKING_ANALYSE_COUNT * 4:
                break
            logger.info(f"Phase 0: searching ranking sources '{q[:55]}'")
            entries = self._ydl_extract_flat(f"ytsearch12:{q}", playlist_end=12)
            for e in entries:
                if not e:
                    continue
                vid_id = e.get("id", "")
                if not vid_id or vid_id in seen_ids:
                    continue
                views = e.get("view_count") or 0
                if views < RANKING_MIN_VIEWS:
                    continue
                title = e.get("title", "")
                if not _is_cat_video(title):
                    continue
                seen_ids.add(vid_id)
                ranking_vids.append({
                    "id":         vid_id,
                    "url":        f"https://www.youtube.com/watch?v={vid_id}",
                    "title":      title,
                    "view_count": views,
                })

        ranking_vids.sort(key=lambda x: x["view_count"], reverse=True)
        to_analyse = ranking_vids[:RANKING_ANALYSE_COUNT]
        logger.info(
            f"Phase 0: {len(ranking_vids)} ranking videos ≥{RANKING_MIN_VIEWS:,} views; "
            f"analysing top {len(to_analyse)}"
        )

        chapter_queries: list[str] = []

        for rv in to_analyse:
            logger.info(
                f"  Analysing: '{rv['title'][:60]}' ({rv['view_count']:,} views)"
            )
            info = self._ydl_get_info(rv["url"])
            if not info:
                continue

            # Step 3: extract source clip IDs from description
            description = info.get("description") or ""
            found_ids = _YT_ID_RE.findall(description)
            logger.info(f"  {len(found_ids)} source ID(s) in description")
            for src_id in found_ids:
                if self._is_used(src_id) or src_id in seen_ids or src_id == rv["id"]:
                    continue
                seen_ids.add(src_id)
                src_url = f"https://www.youtube.com/watch?v={src_id}"
                src_info = self._ydl_get_info(src_url)
                if not src_info:
                    continue
                duration = src_info.get("duration") or 0
                src_title = src_info.get("title") or ""
                if not _is_cat_video(src_title):
                    continue
                if duration and duration > 60:
                    # Longer video — extract clips from it
                    clips = self._clips_from_compilation({
                        "id":         src_id,
                        "url":        src_url,
                        "title":      src_title,
                        "duration":   duration,
                        "view_count": src_info.get("view_count") or 0,
                    })
                    for c in clips:
                        c["_phase0"] = True
                    phase0.extend(clips)
                    logger.info(
                        f"  Source {src_id}: extracted {len(clips)} segments "
                        f"from {duration:.0f}s video"
                    )
                else:
                    # Short individual clip — use as-is
                    phase0.append({
                        "id":         src_id,
                        "url":        src_url,
                        "title":      src_title,
                        "start_time": None,
                        "end_time":   None,
                        "platform":   "youtube",
                        "view_count": src_info.get("view_count") or 0,
                        "like_count": src_info.get("like_count") or 0,
                        "duration":   duration,
                        "_phase0":    True,
                    })

            # Step 4: collect chapter titles → search queries
            chapters = info.get("chapters") or []
            for ch in chapters:
                ch_title = (ch.get("title") or "").strip()
                # Strip ranking prefixes like "#5 —" or "5."
                cleaned = re.sub(r'^#?\d+[\.\-:\s]+', '', ch_title).strip()
                if len(cleaned) >= 4:
                    chapter_queries.append(f"{cleaned} cat funny")

        # Step 4 continued: search for clips matching chapter titles
        # Prefer ≤20s Shorts; fall back to accepting up to 60s with peak detection.
        logger.info(f"Phase 0: searching {len(chapter_queries)} chapter-title queries")
        for cq in chapter_queries[:14]:
            if len(phase0) >= want:
                break
            try:
                entries = self._ydl_extract_flat(f"ytsearch8:{cq}", playlist_end=8)
                for e in entries:
                    if not e:
                        continue
                    vid_id = e.get("id", "")
                    if not vid_id or vid_id in seen_ids or self._is_used(vid_id):
                        continue
                    duration = e.get("duration") or 0
                    # Skip anything confirmed longer than 60s
                    if duration and duration > 60:
                        continue
                    title = e.get("title", "")
                    if not _is_cat_video(title):
                        continue
                    seen_ids.add(vid_id)
                    is_short = not duration or duration <= 20
                    clip: dict = {
                        "id":         vid_id,
                        "url":        f"https://www.youtube.com/watch?v={vid_id}",
                        "title":      title,
                        "start_time": None,
                        "end_time":   None,
                        "platform":   "youtube",
                        "view_count": e.get("view_count") or 0,
                        "like_count": e.get("like_count") or 0,
                        "duration":   duration,
                        "_phase0":    True,
                        "_shorts":    is_short,
                    }
                    # For 20–60s clips, pin the peak funny moment via comments
                    if duration and 20 < duration <= 60:
                        ts_list = self._get_comment_timestamps(clip["url"], duration)
                        if ts_list:
                            bs = max(0.0, ts_list[0] - 3.0)   # 3s before peak
                            be = min(bs + SEGMENT_TARGET_SECS, duration - 1)
                            if be > bs + 4:
                                clip["start_time"] = bs
                                clip["end_time"]   = be
                                clip["id"] = f"{vid_id}_{int(bs)}"
                    phase0.append(clip)
            except Exception as ex:
                logger.debug(f"Phase 0 chapter query failed '{cq}': {ex}")

        logger.info(f"Phase 0: {len(phase0)} high-priority candidates collected")
        return phase0

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
        Return a pool of clip candidates.

        Primary strategy: extract segments from popular compilation videos.
        If yt_queries are provided (from the theme), they're used to guide the
        compilation search so clips match the chosen title theme.
        """
        def _dedup(videos: list[dict]) -> list[dict]:
            seen: set[str] = set()
            out: list[dict] = []
            for v in videos:
                if v["id"] and v["id"] not in seen:
                    seen.add(v["id"])
                    out.append(v)
            return out

        # Phase 0a: dedicated Shorts scraper (≤20s) — highest priority
        # These are the best possible inputs: entire clip IS the funny moment.
        logger.info("Phase 0a: Scraping dedicated funny cat Shorts (≤20s)…")
        shorts_clips = _dedup(self._scrape_cat_shorts(want=want))
        shorts_fresh = [v for v in shorts_clips if self._use_count(v["id"]) == 0]
        logger.info(f"Phase 0a: {len(shorts_fresh)} fresh cat Shorts found")

        # Phase 0b: mine viral ranking videos (500k+ views) for source clips
        logger.info("Phase 0b: Mining viral cat ranking videos for source clips…")
        phase0_clips = _dedup(self._scrape_viral_ranking_sources(want=want))
        phase0_fresh = [v for v in phase0_clips if self._use_count(v["id"]) == 0]
        logger.info(f"Phase 0b: {len(phase0_fresh)} high-priority fresh clips")

        # Phase 1: Individual short viral clips (up to 60s with peak detection)
        # Use theme queries + the broad VIRAL_CAT_QUERIES pool.
        ind_queries = list(yt_queries or []) + random.sample(
            VIRAL_CAT_QUERIES, min(8, len(VIRAL_CAT_QUERIES))
        )
        logger.info("Phase 1: Searching for individual viral cat clips…")
        ind = _dedup(self._scrape_individual_fallback(queries=ind_queries))
        fresh = [v for v in ind if self._use_count(v["id"]) == 0]
        logger.info(f"Phase 1: {len(fresh)} fresh individual clips found")

        # Merge: Shorts first, then Phase 0b, then Phase 1
        combined_p01 = _dedup(shorts_fresh + phase0_fresh + fresh)

        # Phase 2: Compilation extraction fallback
        if len(combined_p01) < want:
            need = want - len(combined_p01)
            logger.info(f"Phase 2: Need {need} more — extracting from compilations…")
            comp_queries = None
            if yt_queries:
                comp_queries = [f"{q} compilation" for q in yt_queries[:3]]
            raw = self._scrape_compilations(queries=comp_queries, want=need)
            fresh_comp = [v for v in _dedup(raw) if self._use_count(v["id"]) == 0]
            logger.info(f"Phase 2: {len(fresh_comp)} fresh compilation clips")
            all_fresh = _dedup(combined_p01 + fresh_comp)
        else:
            all_fresh = combined_p01

        # Phase 3: Reusable clips
        if len(all_fresh) < want:
            reusable = self._get_reusable_candidates()
            used_ids = {v["id"] for v in all_fresh}
            reusable = [v for v in reusable if v["id"] not in used_ids]
            logger.info(f"Phase 3: {len(reusable)} reusable clips available")
            combined = all_fresh + reusable
        else:
            combined = all_fresh

        if not combined:
            logger.warning("No candidates found across all phases")
            return []

        # Priority: ≤20s Shorts → Phase 0b ranking sources → regular fresh → reuse
        # Within each tier, sort by view count descending.
        def _tier(v: dict) -> int:
            if v.get("_reuse"):
                return 3
            dur = v.get("duration") or 0
            if v.get("_shorts") or (dur and dur <= 20):
                return 0   # true Shorts — highest priority
            if v.get("_phase0"):
                return 1   # ranking-sourced clips
            return 2       # general individual / compilation clips

        combined.sort(key=lambda v: (_tier(v), -v.get("view_count", 0)))

        target = max(want * 3, 20)
        pool = combined[:target]

        # Shuffle WITHIN each tier so the same clips don't always appear first,
        # but preserve the inter-tier priority ordering (Tier 0 before Tier 1, etc.)
        from itertools import groupby
        shuffled: list[dict] = []
        for _, group in groupby(pool, key=_tier):
            tier_clips = list(group)
            random.shuffle(tier_clips)
            shuffled.extend(tier_clips)
        pool = shuffled

        logger.info(f"Returning {len(pool)} candidates total")
        return pool
