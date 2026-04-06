#!/usr/bin/env python3
"""
main.py — CatCentral YouTube Shorts Maker

Usage:
  python main.py            Launch the interactive TUI (recommended)
  python main.py setup      First-time setup wizard (headless)
  python main.py run        Run the full pipeline once (headless)
  python main.py schedule   Start the daily scheduler (headless)
  python main.py test       Dry run — scrape + edit but skip upload
  python main.py auth       Re-run YouTube OAuth flow (if token expired)
"""
import logging
import sys
from pathlib import Path

# Make sure the project root is on the path when run directly
sys.path.insert(0, str(Path(__file__).parent))

try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init(autoreset=True)
    def _c(color, s): return color + s + Style.RESET_ALL
    cyan   = lambda s: _c(Fore.CYAN,   s)
    green  = lambda s: _c(Fore.GREEN,  s)
    yellow = lambda s: _c(Fore.YELLOW, s)
    red    = lambda s: _c(Fore.RED,    s)
    bold   = lambda s: Style.BRIGHT + s + Style.RESET_ALL
except ImportError:
    cyan = green = yellow = red = bold = str


BANNER = """\
  ╔══════════════════════════════════════════════╗
  ║   🐱  CatCentral — YouTube Shorts Maker     ║
  ╚══════════════════════════════════════════════╝
"""


def _print_help():
    print(bold(cyan(BANNER)))
    print("  Commands:")
    print(f"    {bold('(none)')}    — Launch the interactive TUI  ← recommended")
    print(f"    {bold('setup')}     — First-time setup wizard (headless)")
    print(f"    {bold('run')}       — Run the full pipeline once now (headless)")
    print(f"    {bold('schedule')}  — Start the daily scheduler (headless)")
    print(f"    {bold('test')}      — Dry run: scrape + edit, no upload")
    print(f"    {bold('auth')}      — Re-authorise YouTube access")
    print()


def cmd_setup():
    from setup_wizard import run
    run()


def cmd_auth(config):
    from src.uploader import YouTubeUploader
    print(cyan("Re-running YouTube OAuth flow …"))
    # Delete existing token so the flow runs fresh
    if config.token_path.exists():
        config.token_path.unlink()
        print(yellow("  Deleted old token."))
    uploader = YouTubeUploader(config)
    if uploader.test_auth():
        print(green("  ✓ Re-authorised successfully."))
    else:
        print(red("  ✗ Authorisation failed. Check your credentials in .env"))
        sys.exit(1)


def cmd_run(config, dry_run: bool = False):
    from src.video_editor import check_ffmpeg
    from src.scheduler import Pipeline

    try:
        check_ffmpeg()
    except RuntimeError as e:
        print(red(str(e)))
        sys.exit(1)

    label = "[DRY RUN] " if dry_run else ""
    print(cyan(f"{label}Running full pipeline …\n"))
    pipeline = Pipeline(config, dry_run=dry_run)
    success = pipeline.run()

    if success:
        print(green("\n  ✓ Pipeline run complete!"))
    else:
        print(red("\n  ✗ Pipeline run failed — check logs for details."))
        sys.exit(1)


def cmd_schedule(config):
    from src.video_editor import check_ffmpeg
    from src.scheduler import Scheduler

    try:
        check_ffmpeg()
    except RuntimeError as e:
        print(red(str(e)))
        sys.exit(1)

    print(bold(cyan(BANNER)))
    print(cyan(f"  Starting scheduler — upload times: {', '.join(config.upload_times)}"))
    print(yellow("  Press Ctrl+C to stop.\n"))

    scheduler = Scheduler(config)
    scheduler.start()


def main():
    args = sys.argv[1:]

    # No arguments → launch the TUI
    if not args:
        from tui import run as tui_run
        tui_run()
        return

    if args[0] in ("-h", "--help", "help"):
        _print_help()
        return

    command = args[0].lower()

    if command == "setup":
        cmd_setup()
        return

    # All other commands need config
    try:
        from config import Config, setup_logging
        config = Config()
        setup_logging(config)
    except Exception as e:
        print(red(f"Failed to load config: {e}"))
        print(yellow("Run 'python main.py setup' first."))
        sys.exit(1)

    if command == "run":
        cmd_run(config, dry_run=False)

    elif command == "test":
        cmd_run(config, dry_run=True)

    elif command == "schedule":
        # Warn if credentials are missing
        issues = config.validate()
        if issues:
            print(red("Configuration issues found:"))
            for issue in issues:
                print(red(f"  • {issue}"))
            print(yellow("Run 'python main.py setup' to fix them."))
            sys.exit(1)
        cmd_schedule(config)

    elif command == "auth":
        cmd_auth(config)

    else:
        print(red(f"Unknown command: {command!r}"))
        _print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
