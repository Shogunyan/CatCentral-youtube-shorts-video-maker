"""
setup_browser_auth.py — One-time browser sign-in for the YouTube upload fallback.

Run this ONCE:
    python setup_browser_auth.py

A real Chrome window opens. Sign into Google/YouTube manually.
Once you're on YouTube Studio the script saves the session and exits.
All future headless uploads reuse those saved cookies automatically.
"""
from pathlib import Path
from config import Config

config = Config()
profile_dir = str(config.data_dir / "browser_profile")

print("Opening browser — sign into YouTube/Google when it appears.")
print("The window will close automatically once you reach YouTube Studio.\n")

from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        profile_dir,
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
    )
    page = ctx.new_page()
    page.goto("https://studio.youtube.com")

    print("Waiting for you to reach YouTube Studio…")
    # Wait until the URL is studio.youtube.com (not accounts.google.com)
    page.wait_for_url("*studio.youtube.com*", timeout=300_000)  # 5 min timeout
    print("\n✓ Signed in! Session saved to:", profile_dir)
    print("You can close this window or it will close in 3 seconds.")
    page.wait_for_timeout(3_000)
    ctx.close()

print("\nDone. Run 'python main.py' — browser uploads will now skip sign-in.")
