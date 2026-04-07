"""
scraper.py — Discovers viral cat video URLs from YouTube Shorts, TikTok, and Instagram.

No videos are downloaded here — only metadata (URL, view count, duration, etc.) is
collected so the caller can decide which clips to actually fetch.

Candidate selection runs in three phases:
  Phase 1 — Current viral: fresh (never-used) clips from trending searches.
  Phase 2 — Older viral:   if Phase 1 comes up short, search timeless/popular
             content with minimum engagement thresholds per platform.
  Phase 3 — Reuse filler:  if still short, allow clips that have been used fewer
             than MAX_CLIP_REUSE times to fill remaining slots.
"""
import json
import logging
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yt_dlp

logger = logging.getLogger(__name__)

# A clip may appear in up to this many ranking videos before being retired.
MAX_CLIP_REUSE = 2

# ── Compilation filter ────────────────────────────────────────────────────────
# Keywords that strongly suggest a video is itself a pre-made compilation.
# These are filtered OUT so we only pull individual viral clips.
_COMPILATION_KEYWORDS = [
    "compilation", "compil", "best of", "try not to laugh",
    "top 10", "top 5", "top 20", "top 15", "top 25", "top 50",
    "funny cats 2024", "funny cats 2025", "funny cat videos 2",
    "funniest cats ever", "#1 to #", "ranked from",
    "part 1", "part 2", "part 3", "ep.", "episode",
    "1 hour", "30 minutes", "minutes of",
]


def _is_compilation(title: str) -> bool:
    """Return True if the video title suggests it's a pre-made compilation."""
    if not title:
        return False
    t = title.lower()
    return any(kw in t for kw in _COMPILATION_KEYWORDS)

# ── Minimum engagement for "older viral" tier ─────────────────────────────────
# YouTube / Instagram: view_count.  TikTok: like_count (falls back to view_count).
OLDER_VIRAL_MIN = {
    "youtube":   50_000,
    "tiktok":   300_000,   # likes; fallback threshold if like_count unavailable
    "instagram":  30_000,
}
# When like_count is unavailable for TikTok, use this view_count proxy instead.
# (300 k likes ≈ 1 M+ views on typical TikTok content.)
TIKTOK_OLDER_VIEW_PROXY = 1_000_000

# ── Current viral search targets ──────────────────────────────────────────────

YOUTUBE_QUERIES = [
    "funny cat video",
    "hilarious cat moment",
    "cat caught on camera funny",
    "my cat did something hilarious",
    "cats being crazy funny",
    "funny cat reaction",
    "cat jump fail",
    "cat zoomies funny",
    "cat scared funny",
    "cats knocking things off",
    "cat attack owner funny",
    "cat playing funny",
    "kitten being adorable funny",
    "cat yelling funny",
]

TIKTOK_HASHTAGS = [
    "funnycat",
    "catsoftiktok",
    "catvideos",
    "funnycats",
    "catmemes",
    "catsbeingcats",
    "funnyanimalvideos",
]

INSTAGRAM_HASHTAGS = [
    "funnycat",
    "catsofinstagram",
    "catvideos",
]

# ── Older viral search targets ────────────────────────────────────────────────
# Queries / hashtags that naturally surface timeless popular content.

YOUTUBE_OLDER_QUERIES = [
    "funniest cat video ever",
    "most viral cat moment",
    "classic funny cat clip",
    "cat video that went viral",
    "the most hilarious cat",
    "cat fails funny",
    "legendary cat moment",
]

TIKTOK_OLDER_HASHTAGS = [
    "bestcats",
    "funnycatvideos",
    "catfunny",
    "catlover",
    "catsoftiktok",
    "catmoment",
]

INSTAGRAM_OLDER_HASHTAGS = [
    "bestcats",
    "catmoments",
    "funnycats",
]


class VideoScraper:
    def __init__(self, config):
        self.config = config
        # _used: {video_id: {"count": int, "url": str, "platform": str,
        #                     "title": str, "view_count": int}}
        self._used: dict[str, dict] = self._load_used()

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load_used(self) -> dict[str, dict]:
        p = self.config.used_videos_path
        if p.exists():
            try:
                data = json.loads(p.read_text())
                if isinstance(data, list):
                    # Migrate old list format — treat every entry as maxed out
                    # (no URL saved, so they can't be reused anyway)
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
        """
        Record that these clips were used in a video.
        Accepts full metadata dicts so URLs and timestamps are preserved.
        """
        now = datetime.now(timezone.utc).isoformat()
        for meta in video_metas:
            vid_id = meta.get("id", "")
            if not vid_id:
                continue
            existing = self._used.get(vid_id, {})
            self._used[vid_id] = {
                "count":        existing.get("count", 0) + 1,
                "first_used_at": existing.get("first_used_at") or now,
                "last_used_at":  now,
                "url":          meta.get("url") or existing.get("url", ""),
                "platform":     meta.get("platform") or existing.get("platform", "unknown"),
                "title":        meta.get("title") or existing.get("title", ""),
                "view_count":   meta.get("view_count") or existing.get("view_count", 0),
            }
        self._save_used()

    def reset_expired_clips(self) -> int:
        """
        Reset the reuse counter for any clip whose first_used_at is older than
        14 days (CLIP_RESET_DAYS).  Returns the number of clips reset.

        Called automatically at the start of each pipeline run so the clip pool
        keeps refreshing without manual intervention.
        """
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
        """True if this clip has hit the reuse limit and should never appear again."""
        return self._used.get(vid_id, {}).get("count", 0) >= MAX_CLIP_REUSE

    def _use_count(self, vid_id: str) -> int:
        return self._used.get(vid_id, {}).get("count", 0)

    # ── Low-level yt-dlp helper ───────────────────────────────────────────────

    def _ydl_extract_flat(self, url: str, playlist_end: int = 25) -> list[dict]:
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

    # ── Platform scrapers ──────────────────────────────────────────────────────

    def scrape_youtube_shorts(
        self, query: str, max_results: int = 20, min_views: int = 0
    ) -> list[dict]:
        """Search YouTube and return metadata for individual viral cat clips.

        Accepts both Shorts (≤60s) and regular short clips (≤120s) so we
        capture viral individual cat videos that aren't posted as Shorts.
        Filters out pre-made compilations by title keyword.
        """
        search_url = f"ytsearch{max_results}:{query}"
        entries = self._ydl_extract_flat(search_url, playlist_end=max_results)

        videos = []
        for e in entries:
            if not e:
                continue
            vid_id = e.get("id", "")
            if not vid_id or self._is_used(vid_id):
                continue
            duration = e.get("duration") or 0
            # Allow individual clips up to 2 minutes; longer = probably a compilation
            if duration and duration > 120:
                continue
            title = e.get("title", "")
            # Skip pre-made compilations
            if _is_compilation(title):
                logger.debug(f"Skipping compilation: {title!r}")
                continue
            view_count = e.get("view_count") or 0
            if view_count < min_views:
                continue
            # Prefer YouTube Shorts URL for vertical content
            url = f"https://www.youtube.com/watch?v={vid_id}"
            videos.append({
                "id":         vid_id,
                "url":        url,
                "title":      title,
                "view_count": view_count,
                "like_count": e.get("like_count") or 0,
                "platform":   "youtube",
                "duration":   duration,
            })
        return sorted(videos, key=lambda x: x["view_count"], reverse=True)

    def scrape_tiktok(
        self, hashtag: str, max_results: int = 20, min_likes: int = 0
    ) -> list[dict]:
        """Scrape a TikTok hashtag feed.

        TikTok frequently blocks scrapers — if it does, we return an empty list
        gracefully so the pipeline falls back to YouTube without crashing.
        """
        url = f"https://www.tiktok.com/tag/{hashtag}"
        try:
            entries = self._ydl_extract_flat(url, playlist_end=max_results)
        except Exception as e:
            logger.warning(f"TikTok #{hashtag} blocked or unavailable: {e}")
            return []

        if not entries:
            logger.debug(f"TikTok #{hashtag}: no entries returned (likely blocked)")
            return []

        videos = []
        for e in entries:
            if not e:
                continue
            vid_id = e.get("id", "")
            if not vid_id or self._is_used(vid_id):
                continue
            duration = e.get("duration") or 0
            if duration and duration > 180:
                continue
            page_url = e.get("url") or e.get("webpage_url") or ""
            if not page_url:
                continue
            title = e.get("title", "")
            if _is_compilation(title):
                logger.debug(f"Skipping TikTok compilation: {title!r}")
                continue

            view_count = e.get("view_count") or 0
            like_count = e.get("like_count") or 0

            # Apply engagement filter: prefer like_count; fall back to view proxy
            if min_likes > 0:
                if like_count and like_count < min_likes:
                    continue
                elif not like_count and view_count < TIKTOK_OLDER_VIEW_PROXY:
                    continue

            videos.append({
                "id":         vid_id,
                "url":        page_url,
                "title":      title,
                "view_count": view_count,
                "like_count": like_count,
                "platform":   "tiktok",
                "duration":   duration,
            })
        return sorted(videos, key=lambda x: x["view_count"], reverse=True)

    def scrape_instagram(
        self, hashtag: str, max_results: int = 20, min_views: int = 0
    ) -> list[dict]:
        """Scrape Instagram hashtag videos (requires instaloader + optional login)."""
        if not self.config.instagram_username:
            logger.debug("Instagram credentials not set, skipping Instagram scrape")
            return []
        try:
            import instaloader

            L = instaloader.Instaloader(download_videos=False)
            if self.config.instagram_password:
                L.login(self.config.instagram_username, self.config.instagram_password)

            tag = instaloader.Hashtag.from_name(L.context, hashtag)
            videos = []
            for post in tag.get_posts():
                if len(videos) >= max_results:
                    break
                if not post.is_video:
                    continue
                pid = str(post.mediaid)
                if self._is_used(pid):
                    continue
                view_count = post.video_view_count or 0
                if view_count < min_views:
                    continue
                videos.append({
                    "id":         pid,
                    "url":        f"https://www.instagram.com/p/{post.shortcode}/",
                    "title":      (post.caption or "")[:100],
                    "view_count": view_count,
                    "like_count": 0,
                    "platform":   "instagram",
                    "duration":   post.video_duration or 0,
                })
            return sorted(videos, key=lambda x: x["view_count"], reverse=True)
        except Exception as e:
            logger.warning(f"Instagram scrape failed for #{hashtag}: {e}")
            return []

    # ── Scraping phases ───────────────────────────────────────────────────────

    def _scrape_current(
        self,
        yt_queries: list[str] | None = None,
        tt_hashtags: list[str] | None = None,
    ) -> list[dict]:
        """Phase 1: scrape currently trending content using theme-matched queries."""
        all_videos: list[dict] = []

        # Use theme-specific queries if provided, else fall back to generic
        queries = yt_queries or random.sample(YOUTUBE_QUERIES, min(3, len(YOUTUBE_QUERIES)))
        for q in queries:
            try:
                vids = self.scrape_youtube_shorts(q, max_results=15)
                all_videos.extend(vids[:8])
                logger.debug(f"[current] YouTube '{q}': {len(vids)} results")
            except Exception as e:
                logger.warning(f"YouTube query '{q}' failed: {e}")

        tags = tt_hashtags or random.sample(TIKTOK_HASHTAGS, min(2, len(TIKTOK_HASHTAGS)))
        for tag in tags:
            try:
                vids = self.scrape_tiktok(tag, max_results=10)
                all_videos.extend(vids[:4])
                logger.debug(f"[current] TikTok #{tag}: {len(vids)} results")
            except Exception as e:
                logger.warning(f"TikTok #{tag} failed: {e}")

        if self.config.instagram_username:
            ig_tag = random.choice(INSTAGRAM_HASHTAGS)
            try:
                vids = self.scrape_instagram(ig_tag, max_results=10)
                all_videos.extend(vids[:3])
                logger.debug(f"[current] Instagram #{ig_tag}: {len(vids)} results")
            except Exception as e:
                logger.warning(f"Instagram #{ig_tag} failed: {e}")

        return all_videos

    def _scrape_older_viral(self) -> list[dict]:
        """
        Phase 2: scrape timeless/older viral content with minimum engagement
        thresholds (YouTube ≥50 k views, TikTok ≥300 k likes, Instagram ≥30 k views).
        """
        all_videos: list[dict] = []
        logger.info("Phase 2: scraping older viral pool…")

        yt_queries = random.sample(YOUTUBE_OLDER_QUERIES, min(3, len(YOUTUBE_OLDER_QUERIES)))
        for q in yt_queries:
            try:
                vids = self.scrape_youtube_shorts(
                    q, max_results=20,
                    min_views=OLDER_VIRAL_MIN["youtube"],
                )
                all_videos.extend(vids[:6])
                logger.debug(f"[older] YouTube '{q}': {len(vids)} results")
            except Exception as e:
                logger.warning(f"[older] YouTube '{q}' failed: {e}")

        tt_tags = random.sample(TIKTOK_OLDER_HASHTAGS, min(2, len(TIKTOK_OLDER_HASHTAGS)))
        for tag in tt_tags:
            try:
                vids = self.scrape_tiktok(
                    tag, max_results=15,
                    min_likes=OLDER_VIRAL_MIN["tiktok"],
                )
                all_videos.extend(vids[:5])
                logger.debug(f"[older] TikTok #{tag}: {len(vids)} results")
            except Exception as e:
                logger.warning(f"[older] TikTok #{tag} failed: {e}")

        if self.config.instagram_username:
            ig_tag = random.choice(INSTAGRAM_OLDER_HASHTAGS)
            try:
                vids = self.scrape_instagram(
                    ig_tag, max_results=10,
                    min_views=OLDER_VIRAL_MIN["instagram"],
                )
                all_videos.extend(vids[:3])
                logger.debug(f"[older] Instagram #{ig_tag}: {len(vids)} results")
            except Exception as e:
                logger.warning(f"[older] Instagram #{ig_tag} failed: {e}")

        return all_videos

    def _get_reusable_candidates(self) -> list[dict]:
        """
        Phase 3: reconstruct candidate dicts for clips that have been used
        fewer than MAX_CLIP_REUSE times.  URLs were saved at mark_used() time.
        """
        reusable = []
        for vid_id, data in self._used.items():
            if data.get("count", 0) < MAX_CLIP_REUSE and data.get("url"):
                reusable.append({
                    "id":         vid_id,
                    "url":        data["url"],
                    "platform":   data.get("platform", "unknown"),
                    "title":      data.get("title", ""),
                    "view_count": data.get("view_count", 0),
                    "like_count": 0,
                    "duration":   0,
                    "_reuse":     True,   # internal flag for logging
                })
        return reusable

    # ── Main public API ───────────────────────────────────────────────────────

    def get_candidates(
        self,
        want: int = 15,
        yt_queries: list[str] | None = None,
        tt_hashtags: list[str] | None = None,
    ) -> list[dict]:
        """
        Return a pool of candidate videos, preferring fresh content.

        If yt_queries / tt_hashtags are provided (from the caption theme),
        those are used instead of the generic search terms so clips match the title.
        """
        def _dedup(videos: list[dict]) -> list[dict]:
            seen: set[str] = set()
            out: list[dict] = []
            for v in videos:
                if v["id"] and v["id"] not in seen:
                    seen.add(v["id"])
                    out.append(v)
            return out

        # ── Phase 1 ───────────────────────────────────────────────────────────
        current = _dedup(self._scrape_current(
            yt_queries=yt_queries, tt_hashtags=tt_hashtags))
        fresh = [v for v in current if self._use_count(v["id"]) == 0]
        logger.info(f"Phase 1: {len(fresh)} fresh current candidates")

        # ── Phase 2 (only if needed) ──────────────────────────────────────────
        if len(fresh) < want:
            older = _dedup(self._scrape_older_viral())
            fresh_older = [v for v in older if self._use_count(v["id"]) == 0]
            logger.info(f"Phase 2: {len(fresh_older)} fresh older-viral candidates")
            # Merge: current fresh first (higher priority), then older fresh
            all_fresh = _dedup(fresh + fresh_older)
        else:
            all_fresh = fresh

        # ── Phase 3 (fill remaining slots with reusable clips) ────────────────
        if len(all_fresh) < want:
            reusable = self._get_reusable_candidates()
            # Exclude any IDs already in all_fresh
            fresh_ids = {v["id"] for v in all_fresh}
            reusable = [v for v in reusable if v["id"] not in fresh_ids]
            logger.info(
                f"Phase 3: {len(reusable)} reusable clips available as filler"
            )
            combined = all_fresh + reusable
        else:
            combined = all_fresh

        if not combined:
            logger.warning("No candidates found across all phases")
            return []

        # Sort fresh clips by view count, reuse fillers after
        fresh_pool  = [v for v in combined if not v.get("_reuse")]
        reuse_pool  = [v for v in combined if v.get("_reuse")]
        fresh_pool.sort(key=lambda x: x["view_count"], reverse=True)

        # Take top-N fresh, pad with reuse
        target = max(want * 3, 20)
        pool = fresh_pool[:target] + reuse_pool[: max(0, target - len(fresh_pool))]

        random.shuffle(pool)
        logger.info(
            f"Returning {len(pool)} candidates "
            f"({len(fresh_pool)} fresh + {len(reuse_pool)} reusable)"
        )
        return pool
