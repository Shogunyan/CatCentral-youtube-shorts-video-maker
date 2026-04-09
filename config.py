"""
config.py — Loads configuration from .env and provides typed access.
"""
import os
import re
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent / ".env")

_TIME_RE = re.compile(r'^\d{1,2}:\d{2}$')


class Config:
    # ── Paths (class-level, path-only; safe to share) ─────────
    base_dir: Path = Path(__file__).parent

    def __init__(self):
        # ── YouTube OAuth ──────────────────────────────────────
        self.google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")
        self.google_client_secret: str = os.getenv("GOOGLE_CLIENT_SECRET", "")

        # ── Instagram (optional — scraper pending Gemini integration) ──
        self.instagram_username: str = os.getenv("INSTAGRAM_USERNAME", "")
        self.instagram_password: str = os.getenv("INSTAGRAM_PASSWORD", "")

        # ── Schedule (instance-level — never shared between instances) ──
        raw_times = os.getenv("UPLOAD_TIMES", "09:00,14:00,19:00")
        self.upload_times: list[str] = [
            t.strip() for t in raw_times.split(",") if t.strip()
        ]

        # ── AI visual analysis (optional) ─────────────────────
        # When set, Gemini Vision is used to identify the exact cat action in
        # each ranking-video frame so we can find the original standalone clip.
        self.gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")

        # ── Video ──────────────────────────────────────────────
        self.clips_per_video: int = int(os.getenv("CLIPS_PER_VIDEO", "5"))
        self.clip_duration: int = int(os.getenv("CLIP_DURATION", "25"))
        self.watermark_text: str = os.getenv("WATERMARK_TEXT", "@CatCentral")

        # ── Paths ──────────────────────────────────────────────
        self.data_dir: Path = self.base_dir / "data"
        self.download_dir: Path = self.data_dir / "downloaded"
        self.processed_dir: Path = self.data_dir / "processed"
        self.used_videos_path: Path = self.data_dir / "used_videos.json"
        self.viral_clips_path: Path = self.data_dir / "viral_clips.json"
        self.token_path: Path = self.data_dir / "youtube_token.json"
        self.log_path: Path = self.base_dir / "logs" / "app.log"

        # ── Logging ────────────────────────────────────────────
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO")

        # Ensure directories exist
        for d in (self.data_dir, self.download_dir, self.processed_dir,
                  self.base_dir / "logs"):
            d.mkdir(parents=True, exist_ok=True)

    def validate(self) -> list[str]:
        """Return a list of missing/invalid config items."""
        issues = []
        if not self.google_client_id:
            issues.append("GOOGLE_CLIENT_ID not set in .env")
        if not self.google_client_secret:
            issues.append("GOOGLE_CLIENT_SECRET not set in .env")
        # Validate upload times format (HH:MM)
        bad_times = [t for t in self.upload_times if not _TIME_RE.match(t)]
        if bad_times:
            issues.append(
                f"Invalid upload times (must be HH:MM): {', '.join(bad_times)}"
            )
        return issues


def setup_logging(config: Config):
    level = getattr(logging, config.log_level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    logging.basicConfig(
        level=level,
        format=fmt,
        force=True,   # override any handlers already attached (e.g. by Textual)
        handlers=[
            logging.FileHandler(config.log_path),
            logging.StreamHandler(),
        ],
    )
