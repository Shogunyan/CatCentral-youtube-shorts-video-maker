import os
from pathlib import Path
from dotenv import load_dotenv

_root = Path(__file__).parent

# Load parent project .env first, then local v2 override
load_dotenv(_root.parent / ".env")
load_dotenv(_root / ".env", override=True)


class Config:
    # YouTube OAuth - shared with parent (uploads to the same channel)
    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")
    google_client_secret: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    youtube_email: str = os.getenv("YOUTUBE_EMAIL", "")
    youtube_password: str = os.getenv("YOUTUBE_PASSWORD", "")

    # Optional: Claude AI for smarter, context-aware title generation
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")

    # Branding
    watermark_text: str = os.getenv("V2_WATERMARK_TEXT", "@CatCentral")

    # Schedule: comma-separated 24h times (EST recommended: 12:00, 18:00)
    upload_times: list = [
        t.strip() for t in os.getenv("V2_UPLOAD_TIMES", "12:00,18:00").split(",")
    ]

    # Content quality bar - only use clips with this many source views
    min_source_views: int = int(os.getenv("V2_MIN_SOURCE_VIEWS", "500000"))

    # Target clip duration in seconds (sweet spot for Shorts completion rate)
    target_duration: int = int(os.getenv("V2_TARGET_DURATION", "20"))
    max_duration: int = int(os.getenv("V2_MAX_DURATION", "30"))

    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # Paths
    data_dir: Path = _root / "data"
    used_clips_path: Path = data_dir / "used_clips.json"
    performance_path: Path = data_dir / "performance.json"
    download_dir: Path = data_dir / "downloads"
    processed_dir: Path = data_dir / "processed"

    # Share the YouTube OAuth token with the parent project (same channel)
    youtube_token_path: Path = _root.parent / "data" / "youtube_token.json"


config = Config()
