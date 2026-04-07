"""
scraper.py — Discovers viral cat clips by extracting segments from popular
YouTube compilation videos.

Strategy:
  Phase 1 — Compilation extraction: find popular cat compilation videos (1–20 min),
             extract individual clip segments using chapter markers or even splits.
  Phase 2 — Individual fallback: if Phase 1 doesn't yield enough clips, search
             for short individual cat clips directly.
  Phase 3 — Reuse: allow previously-used clips (up to MAX_CLIP_REUSE times).
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

# Max seconds per compilation segment — must stay close to clip_duration (10s)
# so one segment = one cat moment, not two cats crammed together.
SEGMENT_TARGET_SECS = 11

# ── Primary: individual short viral cat clips ─────────────────────────────────
# Short individual videos (5–60s) where the WHOLE clip IS the funny moment.
# No slicing needed. Great for iconic memes from 2017-2022.
VIRAL_CAT_QUERIES = [
    # Classic/iconic meme-era searches
    "funny cat video 2019",
    "funny cat video 2018",
    "viral cat video 2017",
    "classic funny cat meme original",
    "iconic cat video",
    "viral cat moment original",
    # Specific behaviours that appear in every ranking video
    "cat scared funny original video",
    "cat yelling funny video",
    "cat making weird noise funny",
    "cat jump scare funny",
    "cat obsessed funny video",
    "cat zoomies crazy funny",
    "cat knocking things off table funny",
    "cat fights reflection mirror funny",
    "cat attacks owner funny",
    "cat stuck in box funny",
    "cat refuses to move funny",
    "cat falls off counter funny",
    "cat dramatically rolls over",
    "cat surprised reaction funny",
    "cat hissing funny",
    "cat chirping at birds funny",
    "cat biscuits funny",
]

# ── Secondary: compilation extraction ────────────────────────────────────────
# Used only if individual search doesn't find enough fresh clips.
COMPILATION_QUERIES = [
    "funny cat tiktok compilation 2025",
    "viral cat moments compilation 2024",
    "best cat clips compilation 2024",
    "funniest cats compilation no commentary",
    "cats being cats tiktok compilation",
    "cat fails funny compilation",
    "daily dose of internet cats",
]


def _is_unwanted(title: str) -> bool:
    """Return True if this video should be skipped (ranking/reaction content)."""
    if not title:
        return False
    t = title.lower()
    BLOCK = [
        "try not to laugh", "react", "reaction",
        "ranked", "ranking", "worst to best", "tier list",
        "#1 to #", "top 10", "top 5", "top 20",
    ]
    return any(kw in t for kw in BLOCK)


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
                    "end_time":   min(end, start + 25),
                    "platform":   "youtube",
                    "view_count": view_count,
                    "like_count": info.get("like_count") or 0,
                    "duration":   min(seg_len, 25),
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
                    start = ts
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
        return sorted(all_videos, key=lambda x: x["view_count"], reverse=True)

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

        # Phase 1: Individual short viral clips (primary)
        # These are complete 5–60s videos where the whole clip = the funny moment.
        # Use theme queries + the broad VIRAL_CAT_QUERIES pool.
        ind_queries = list(yt_queries or []) + random.sample(
            VIRAL_CAT_QUERIES, min(8, len(VIRAL_CAT_QUERIES))
        )
        logger.info("Phase 1: Searching for individual viral cat clips…")
        ind = _dedup(self._scrape_individual_fallback(queries=ind_queries))
        fresh = [v for v in ind if self._use_count(v["id"]) == 0]
        logger.info(f"Phase 1: {len(fresh)} fresh individual clips found")

        # Phase 2: Compilation extraction fallback
        if len(fresh) < want:
            need = want - len(fresh)
            logger.info(f"Phase 2: Need {need} more — extracting from compilations…")
            comp_queries = None
            if yt_queries:
                comp_queries = [f"{q} compilation" for q in yt_queries[:3]]
            raw = self._scrape_compilations(queries=comp_queries, want=need)
            fresh_comp = [v for v in _dedup(raw) if self._use_count(v["id"]) == 0]
            logger.info(f"Phase 2: {len(fresh_comp)} fresh compilation clips")
            all_fresh = _dedup(fresh + fresh_comp)
        else:
            all_fresh = fresh

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

        # Prioritise highest-viewed source videos (more viral = better clips)
        fresh_pool = [v for v in combined if not v.get("_reuse")]
        reuse_pool = [v for v in combined if v.get("_reuse")]
        fresh_pool.sort(key=lambda x: x.get("view_count", 0), reverse=True)

        target = max(want * 3, 20)
        pool = fresh_pool[:target] + reuse_pool[:max(0, target - len(fresh_pool))]
        random.shuffle(pool)

        logger.info(f"Returning {len(pool)} candidates total")
        return pool
