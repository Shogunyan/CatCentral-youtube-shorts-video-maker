"""
setup_wizard.py — Interactive first-time setup for CatCentral.

Guides the user through:
  1. Creating / locating Google Cloud credentials for YouTube upload
  2. Running the OAuth2 browser flow
  3. Optionally setting Instagram credentials
  4. Writing a .env file
"""
import sys
import webbrowser
from pathlib import Path

try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init(autoreset=True)
    def green(s):  return Fore.GREEN + s + Style.RESET_ALL
    def yellow(s): return Fore.YELLOW + s + Style.RESET_ALL
    def cyan(s):   return Fore.CYAN + s + Style.RESET_ALL
    def bold(s):   return Style.BRIGHT + s + Style.RESET_ALL
    def red(s):    return Fore.RED + s + Style.RESET_ALL
except ImportError:
    green = yellow = cyan = bold = red = str


ENV_PATH = Path(__file__).parent / ".env"


def _ask(prompt: str, default: str = "") -> str:
    display = f"{prompt} [{default}]: " if default else f"{prompt}: "
    try:
        val = input(cyan(display)).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(0)
    return val or default


def _confirm(prompt: str) -> bool:
    ans = _ask(prompt + " (y/n)", "y")
    return ans.lower().startswith("y")


def _divider():
    print(cyan("─" * 60))


def run():
    print()
    print(bold("╔══════════════════════════════════════════════════════╗"))
    print(bold("║     🐱  CatCentral YouTube Shorts Maker — Setup     ║"))
    print(bold("╚══════════════════════════════════════════════════════╝"))
    print()
    print("This wizard will configure your CatCentral account.")
    print("It only needs to run once.\n")

    # ── Step 1: Google Cloud credentials ─────────────────────────────────────
    _divider()
    print(bold("STEP 1 — YouTube API Credentials"))
    print()
    print("You need a Google Cloud project with the YouTube Data API v3 enabled.")
    print()
    print("  If you haven't set one up yet, follow these steps:")
    print(yellow("  1. Go to https://console.cloud.google.com/"))
    print(yellow("  2. Create a new project (or select an existing one)"))
    print(yellow("  3. Go to 'APIs & Services' → 'Library'"))
    print(yellow("  4. Search for 'YouTube Data API v3' and ENABLE it"))
    print(yellow("  5. Go to 'APIs & Services' → 'Credentials'"))
    print(yellow("  6. Click 'Create Credentials' → 'OAuth client ID'"))
    print(yellow("  7. Choose Application type: 'Desktop app'"))
    print(yellow("  8. Copy the Client ID and Client Secret shown"))
    print()

    open_browser = _confirm("Open Google Cloud Console in your browser now?")
    if open_browser:
        webbrowser.open("https://console.cloud.google.com/apis/credentials")
        print(green("  Browser opened. Come back here when you have your credentials."))
        input(cyan("  Press Enter when ready …"))

    print()
    client_id = _ask("Paste your Google Client ID")
    client_secret = _ask("Paste your Google Client Secret")

    if not client_id or not client_secret:
        print(red("Client ID and Secret are required. Setup aborted."))
        sys.exit(1)

    # ── Step 2: OAuth flow ────────────────────────────────────────────────────
    _divider()
    print(bold("STEP 2 — Authorise YouTube Channel Access"))
    print()
    print("A browser window will open asking you to log in with the Google")
    print("account that owns your YouTube channel, then grant CatCentral")
    print("permission to upload videos on your behalf.")
    print()
    print(yellow("  • Log in with the account that manages your channel"))
    print(yellow("  • Click 'Allow' on the permissions screen"))
    print(yellow("  • You may see a 'This app isn't verified' warning — click"))
    print(yellow("    'Advanced' → 'Go to CatCentral (unsafe)' to proceed"))
    print()
    input(cyan("  Press Enter to open the browser for authorisation …"))

    # Write a temporary .env so Config can pick up the credentials
    _write_env(
        client_id=client_id,
        client_secret=client_secret,
        upload_times="09:00,14:00,19:00",
        # Instagram and Gemini preserved from existing .env if present
    )

    # Trigger the OAuth flow
    try:
        from config import Config
        from src.uploader import YouTubeUploader

        cfg = Config()
        uploader = YouTubeUploader(cfg)
        print()
        if uploader.test_auth():
            print(green("  ✓ YouTube authorisation successful!"))
        else:
            print(red("  ✗ Authorisation completed but channel verification failed."))
            print(yellow("    Check that the account has a YouTube channel."))
    except Exception as e:
        print(red(f"  ✗ OAuth error: {e}"))
        print(yellow("    Check your Client ID and Secret and try again."))
        sys.exit(1)

    # ── Step 3: Instagram (optional) ─────────────────────────────────────────
    _divider()
    print(bold("STEP 3 — Instagram (Optional)"))
    print()
    print("Instagram credentials unlock a future clip source powered by Gemini Vision.")
    print("When enabled, the scraper will pull viral cat Reels and use Gemini to")
    print("validate each clip (real cat, not animated, English) before using it.")
    print(yellow("  Note: Instagram scraping is rate-limited — credentials reduce errors."))
    print()

    ig_username = ""
    ig_password = ""
    if _confirm("Add Instagram credentials now?"):
        ig_username = _ask("Instagram username")
        ig_password = _ask("Instagram password")

    # ── Step 4: Schedule ──────────────────────────────────────────────────────
    _divider()
    print(bold("STEP 4 — Upload Schedule"))
    print()
    print("CatCentral will upload ranking videos per day automatically.")
    print("Default schedule: 09:00, 14:00, 19:00 (system time)")
    print()
    custom = _ask(
        "Upload times (comma-separated 24h, e.g. 08:00,13:00,20:00)",
        "09:00,14:00,19:00",
    )

    # ── Write final .env ──────────────────────────────────────────────────────
    _write_env(
        client_id=client_id,
        client_secret=client_secret,
        instagram_username=ig_username,
        instagram_password=ig_password,
        upload_times=custom,
    )

    _divider()
    print()
    print(green(bold("  ✓ Setup complete!  CatCentral is ready to run.")))
    print()
    print("  To start the daily scheduler (runs indefinitely):")
    print(bold(cyan("    python main.py schedule")))
    print()
    print("  To do a single manual run right now:")
    print(bold(cyan("    python main.py run")))
    print()
    print("  To do a dry run (no upload):")
    print(bold(cyan("    python main.py test")))
    print()


def _write_env(
    client_id: str,
    client_secret: str,
    upload_times: str = "",
    instagram_username: str = "",
    instagram_password: str = "",
    gemini_api_key: str = "",
    watermark_text: str = "",
    clip_duration: str = "",
):
    # Preserve existing values if the caller doesn't supply new ones.
    existing: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                existing[k.strip()] = v.strip()

    def _keep(new_val: str, key: str, default: str = "") -> str:
        """Use new_val if non-empty, else fall back to existing .env value, else default."""
        return new_val if new_val else existing.get(key, default)

    gemini_api_key    = _keep(gemini_api_key,    "GEMINI_API_KEY")
    instagram_username = _keep(instagram_username, "INSTAGRAM_USERNAME")
    instagram_password = _keep(instagram_password, "INSTAGRAM_PASSWORD")
    upload_times       = _keep(upload_times,       "UPLOAD_TIMES",       "09:00,14:00,19:00")
    watermark_text     = _keep(watermark_text,     "WATERMARK_TEXT",     "@CatCentral")
    clip_duration      = _keep(clip_duration,      "CLIP_DURATION",      "25")
    clips_per_video    = existing.get("CLIPS_PER_VIDEO", "5")
    log_level          = existing.get("LOG_LEVEL",        "INFO")

    content = f"""# CatCentral YouTube Shorts Maker — generated by setup_wizard.py
GOOGLE_CLIENT_ID={client_id}
GOOGLE_CLIENT_SECRET={client_secret}
INSTAGRAM_USERNAME={instagram_username}
INSTAGRAM_PASSWORD={instagram_password}
UPLOAD_TIMES={upload_times}
CLIPS_PER_VIDEO={clips_per_video}
CLIP_DURATION={clip_duration}
WATERMARK_TEXT={watermark_text}
LOG_LEVEL={log_level}
# Optional: Gemini API key enables AI visual clip-source detection + AI video filtering.
# Get a free key at aistudio.google.com — same Google account as YouTube.
# Without it the system still works — just uses text-based search instead.
GEMINI_API_KEY={gemini_api_key}
"""
    ENV_PATH.write_text(content)
    print(green(f"  ✓ .env written to {ENV_PATH}"))


if __name__ == "__main__":
    run()
