"""
config.py — Loads configuration from .env and provides typed access.
"""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent / ".env")


class Config:
    # ── YouTube OAuth ──────────────────────────────────────────
    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")
    google_client_secret: str = os.getenv("GOOGLE_CLIENT_SECRET", "")

    # ── Instagram (optional) ───────────────────────────────────
    instagram_username: str = os.getenv("INSTAGRAM_USERNAME", "")
    instagram_password: str = os.getenv("INSTAGRAM_PASSWORD", "")

    # ── Schedule ───────────────────────────────────────────────
    upload_times: list[str] = [
        t.strip()
        for t in os.getenv("UPLOAD_TIMES", "09:00,14:00,19:00").split(",")
    ]

    # ── Video ──────────────────────────────────────────────────
    clips_per_video: int = int(os.getenv("CLIPS_PER_VIDEO", "5"))
    clip_duration: int = int(os.getenv("CLIP_DURATION", "10"))
    watermark_text: str = os.getenv("WATERMARK_TEXT", "@CatCentral")

    # ── Paths ──────────────────────────────────────────────────
    base_dir: Path = Path(__file__).parent
    data_dir: Path = base_dir / "data"
    download_dir: Path = data_dir / "downloaded"
    processed_dir: Path = data_dir / "processed"
    used_videos_path: Path = data_dir / "used_videos.json"
    token_path: Path = data_dir / "youtube_token.json"
    log_path: Path = base_dir / "logs" / "app.log"

    # ── Logging ────────────────────────────────────────────────
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    def __init__(self):
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
        return issues


def setup_logging(config: Config):
    level = getattr(logging, config.log_level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    logging.basicConfig(
        level=level,
        format=fmt,
        handlers=[
            logging.FileHandler(config.log_path),
            logging.StreamHandler(),
        ],
    )
