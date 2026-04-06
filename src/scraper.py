"""
scraper.py — Discovers viral cat video URLs from YouTube Shorts, TikTok, and Instagram.

No videos are downloaded here — only metadata (URL, view count, duration, etc.) is
collected so the caller can decide which clips to actually fetch.
"""
import json
import logging
import random
from pathlib import Path

import yt_dlp

logger = logging.getLogger(__name__)

# ── Search targets ────────────────────────────────────────────────────────────

YOUTUBE_QUERIES = [
    "funny cat videos shorts",
    "viral cat moments #shorts",
    "cats being cats funny shorts",
    "hilarious cat fails shorts",
    "cats being goofy shorts",
    "funny cat compilation shorts",
    "cute cat moments viral shorts",
    "cat surprises owner shorts",
    "cat attack funny shorts",
    "cats vs cucumbers shorts",
    "cats knocking things off shorts",
    "cats startled funny shorts",
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


class VideoScraper:
    def __init__(self, config):
        self.config = config
        self._used: set[str] = self._load_used()

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load_used(self) -> set[str]:
        p = self.config.used_videos_path
        if p.exists():
            try:
                return set(json.loads(p.read_text()))
            except Exception:
                pass
        return set()

    def _save_used(self):
        p = self.config.used_videos_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(sorted(self._used)))

    def mark_used(self, video_ids: list[str]):
        self._used.update(video_ids)
        self._save_used()

    # ── Platform scrapers ──────────────────────────────────────────────────────

    def _ydl_extract_flat(self, url: str, playlist_end: int = 25) -> list[dict]:
        """Run yt-dlp in flat-extract mode and return the entries list."""
        ydl_opts = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
            "playlistend": playlist_end,
            "ignoreerrors": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(url, download=False)
                if result:
                    return result.get("entries") or []
        except Exception as e:
            logger.debug(f"yt-dlp flat extract failed for {url}: {e}")
        return []

    def scrape_youtube_shorts(self, query: str, max_results: int = 20) -> list[dict]:
        """Search YouTube and return metadata for cat-relevant short clips."""
        search_url = f"ytsearch{max_results}:{query}"
        entries = self._ydl_extract_flat(search_url, playlist_end=max_results)

        videos = []
        for e in entries:
            if not e:
                continue
            vid_id = e.get("id", "")
            if not vid_id or vid_id in self._used:
                continue
            duration = e.get("duration") or 0
            if duration and duration > 60:
                continue  # skip non-Shorts
            videos.append(
                {
                    "id": vid_id,
                    "url": f"https://www.youtube.com/shorts/{vid_id}",
                    "title": e.get("title", ""),
                    "view_count": e.get("view_count") or 0,
                    "platform": "youtube",
                    "duration": duration,
                }
            )
        return sorted(videos, key=lambda x: x["view_count"], reverse=True)

    def scrape_tiktok(self, hashtag: str, max_results: int = 20) -> list[dict]:
        """Scrape a TikTok hashtag feed."""
        url = f"https://www.tiktok.com/tag/{hashtag}"
        entries = self._ydl_extract_flat(url, playlist_end=max_results)

        videos = []
        for e in entries:
            if not e:
                continue
            vid_id = e.get("id", "")
            if not vid_id or vid_id in self._used:
                continue
            duration = e.get("duration") or 0
            if duration and duration > 180:
                continue
            page_url = e.get("url") or e.get("webpage_url") or ""
            if not page_url:
                continue
            videos.append(
                {
                    "id": vid_id,
                    "url": page_url,
                    "title": e.get("title", ""),
                    "view_count": e.get("view_count") or 0,
                    "platform": "tiktok",
                    "duration": duration,
                }
            )
        return sorted(videos, key=lambda x: x["view_count"], reverse=True)

    def scrape_instagram(self, hashtag: str, max_results: int = 20) -> list[dict]:
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
                if pid in self._used:
                    continue
                videos.append(
                    {
                        "id": pid,
                        "url": f"https://www.instagram.com/p/{post.shortcode}/",
                        "title": (post.caption or "")[:100],
                        "view_count": post.video_view_count or 0,
                        "platform": "instagram",
                        "duration": post.video_duration or 0,
                    }
                )
            return sorted(videos, key=lambda x: x["view_count"], reverse=True)
        except Exception as e:
            logger.warning(f"Instagram scrape failed for #{hashtag}: {e}")
            return []

    # ── Aggregate scraper ─────────────────────────────────────────────────────

    def get_candidates(self, want: int = 15) -> list[dict]:
        """
        Collect more candidates than needed across all platforms.
        Returns a deduplicated, view-count-sorted list.
        """
        all_videos: list[dict] = []

        # YouTube — pick 3 random queries
        yt_queries = random.sample(YOUTUBE_QUERIES, min(3, len(YOUTUBE_QUERIES)))
        for q in yt_queries:
            try:
                vids = self.scrape_youtube_shorts(q, max_results=15)
                all_videos.extend(vids[:6])
                logger.debug(f"YouTube '{q}': {len(vids)} results")
            except Exception as e:
                logger.warning(f"YouTube query '{q}' failed: {e}")

        # TikTok — pick 2 random hashtags
        tt_tags = random.sample(TIKTOK_HASHTAGS, min(2, len(TIKTOK_HASHTAGS)))
        for tag in tt_tags:
            try:
                vids = self.scrape_tiktok(tag, max_results=10)
                all_videos.extend(vids[:4])
                logger.debug(f"TikTok #{tag}: {len(vids)} results")
            except Exception as e:
                logger.warning(f"TikTok #{tag} failed: {e}")

        # Instagram — optional
        if self.config.instagram_username:
            ig_tag = random.choice(INSTAGRAM_HASHTAGS)
            try:
                vids = self.scrape_instagram(ig_tag, max_results=10)
                all_videos.extend(vids[:3])
                logger.debug(f"Instagram #{ig_tag}: {len(vids)} results")
            except Exception as e:
                logger.warning(f"Instagram #{ig_tag} failed: {e}")

        # Deduplicate by ID and exclude already-used
        seen: set[str] = set()
        unique: list[dict] = []
        for v in all_videos:
            vid_id = v["id"]
            if vid_id and vid_id not in seen and vid_id not in self._used:
                seen.add(vid_id)
                unique.append(v)

        # Sort by view count descending, then shuffle top-N slightly for variety
        unique.sort(key=lambda x: x["view_count"], reverse=True)
        top_pool = unique[: max(want * 3, 20)]
        random.shuffle(top_pool)
        logger.info(f"Scraper found {len(unique)} unique candidates, returning top {len(top_pool)}")
        return top_pool
