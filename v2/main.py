#!/usr/bin/env python3
"""
CatCentral v2 — viral-optimised YouTube Shorts automation.

Usage:
  python main.py run        # Find, process, and upload one video now
  python main.py test       # Dry run — render video locally, skip upload
  python main.py schedule   # Start the daily scheduler daemon
  python main.py auth       # Re-authorise YouTube OAuth
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import config


def _setup_logging():
    level = getattr(logging, config.log_level.upper(), logging.INFO)
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir / "v2.log", encoding="utf-8"),
        ],
    )


def main():
    parser = argparse.ArgumentParser(
        description="CatCentral v2 — viral-optimised YouTube Shorts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "command",
        choices=["run", "test", "schedule", "auth"],
        nargs="?",
        default="run",
        help="Action to perform (default: run)",
    )
    args = parser.parse_args()
    _setup_logging()

    logger = logging.getLogger("v2.main")
    logger.info(f"CatCentral v2 | command={args.command}")

    if args.command == "auth":
        from src.uploader import YouTubeUploader
        svc = YouTubeUploader(config)._build_service()
        if svc:
            logger.info("YouTube authentication successful")
        else:
            logger.error("Authentication failed — check GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in .env")
            sys.exit(1)
        return

    from src.scheduler import Pipeline
    pipeline = Pipeline(config)

    if args.command == "run":
        sys.exit(0 if pipeline.run() else 1)
    elif args.command == "test":
        sys.exit(0 if pipeline.run_dry() else 1)
    elif args.command == "schedule":
        pipeline.start_scheduler()


if __name__ == "__main__":
    main()
