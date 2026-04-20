"""
uploader.py — Authenticates with YouTube via OAuth2 and uploads Shorts.

OAuth flow:
  1. First run: opens browser for user to approve access → saves token.
  2. Subsequent runs: loads saved token, auto-refreshes if expired.

The user never needs to re-authenticate unless they revoke access or delete
data/youtube_token.json.
"""
import json
import logging
import os
import subprocess
import webbrowser
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)


def _is_wsl() -> bool:
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except Exception:
        return False


def _open_browser(url: str) -> None:
    """Open a URL — WSL, Mac, Linux, with URL fallback print."""
    import platform

    # Always save the URL to a file so it can be retrieved if the browser doesn't open
    try:
        with open("/tmp/catcentral_auth_url.txt", "w") as f:
            f.write(url + "\n")
    except Exception:
        pass

    if _is_wsl():
        for cmd in (
            ["explorer.exe", url],
            ["wslview", url],
            ["powershell.exe", "-Command", f'Start-Process "{url}"'],
        ):
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except FileNotFoundError:
                continue
            except Exception:
                continue
    elif platform.system() == "Darwin":
        # macOS — use the `open` command directly (avoids monkey-patched webbrowser.open)
        try:
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            pass
    else:
        # Native Linux — xdg-open, then webbrowser controller (not the patched module fn)
        try:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except FileNotFoundError:
            pass
        try:
            webbrowser.get().open(url)
            return
        except Exception:
            pass

    # Last resort — print the URL so the user can open it manually
    print(f"\n  Please open this URL in your browser to authenticate:\n  {url}\n")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",   # needed for view-count checks
]
API_SERVICE_NAME = "youtube"
API_VERSION = "v3"

# YouTube category IDs
CATEGORY_PETS_ANIMALS = "15"


class YouTubeUploader:
    def __init__(self, config):
        self.config = config
        self._service = None

    # ── Authentication ─────────────────────────────────────────────────────────

    def _get_credentials(self) -> Credentials:
        token_path: Path = self.config.token_path
        creds = None

        if token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
            except Exception as e:
                logger.warning(f"Could not load saved token: {e}")

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self._save_token(creds)
                return creds
            except Exception as e:
                logger.warning(f"Token refresh failed: {e}")
                creds = None

        # Build client config dict from individual env vars
        client_config = {
            "installed": {
                "client_id": self.config.google_client_id,
                "client_secret": self.config.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
            }
        }

        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
        # Patch webbrowser.open so WSL redirects to the Windows browser
        _orig = webbrowser.open
        webbrowser.open = lambda url, new=0, autoraise=True: _open_browser(url) or True
        try:
            creds = flow.run_local_server(port=0, open_browser=True)
        finally:
            webbrowser.open = _orig
        self._save_token(creds)
        return creds

    def _save_token(self, creds: Credentials):
        self.config.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.token_path.write_text(creds.to_json())
        logger.debug(f"Token saved to {self.config.token_path}")

    def _get_service(self):
        if self._service is None:
            creds = self._get_credentials()
            self._service = build(API_SERVICE_NAME, API_VERSION, credentials=creds)
        return self._service

    # ── Upload ─────────────────────────────────────────────────────────────────

    def upload(
        self,
        video_path: Path,
        title: str,
        description: str,
        tags: list[str],
        made_for_kids: bool = False,
    ) -> str | None:
        """
        Upload a video to YouTube as a Short.

        Returns the YouTube video ID on success, None on failure.
        """
        # Ensure #shorts is in title for Shorts eligibility
        if "#shorts" not in title.lower():
            title = title.rstrip() + " #shorts"

        # YouTube title max length is 100 characters
        if len(title) > 100:
            title = title[:97] + "..."

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": CATEGORY_PETS_ANIMALS,
                "defaultLanguage": "en",
                "defaultAudioLanguage": "en",
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": made_for_kids,
            },
        }

        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            resumable=True,
            chunksize=10 * 1024 * 1024,  # 10 MB chunks
        )

        logger.info(f"Uploading: {title!r} ({video_path.name})")
        try:
            service = self._get_service()
            request = service.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media,
            )

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    pct = int(status.progress() * 100)
                    logger.info(f"  Upload progress: {pct}%")

            if not response:
                logger.error("Upload loop exited but response is empty")
                return None
            video_id = response.get("id", "")
            logger.info(f"  ✓ Uploaded! https://www.youtube.com/shorts/{video_id}")
            return video_id

        except HttpError as e:
            logger.error(f"YouTube API error: {e.resp.status} — {e.content.decode('utf-8', errors='replace')}")
            logger.info("API upload failed — attempting headless browser fallback…")
            return self._browser_upload(video_path, title, description)
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            logger.info("Upload exception — attempting headless browser fallback…")
            return self._browser_upload(video_path, title, description)

    def _browser_upload(
        self,
        video_path: Path,
        title: str,
        description: str,
    ) -> str | None:
        """
        Upload via headless Chromium when the YouTube API quota is exceeded.
        Signs into YouTube Studio with the configured account credentials and
        uploads the video through the web UI.
        """
        email = self.config.youtube_email
        password = self.config.youtube_password
        if not email or not password:
            logger.error("Browser upload requires YOUTUBE_EMAIL and YOUTUBE_PASSWORD in .env")
            return None

        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            logger.error("playwright not installed — run: pip install playwright && python -m playwright install chromium")
            return None

        logger.info(f"Browser upload: signing in as {email}…")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page = ctx.new_page()

            def _screenshot(tag: str) -> None:
                try:
                    p = f"/tmp/yt_upload_{tag}.png"
                    page.screenshot(path=p)
                    logger.debug(f"  Screenshot → {p}")
                except Exception:
                    pass

            try:
                # ── Sign in ───────────────────────────────────────────────────
                page.goto("https://accounts.google.com/signin/v2/identifier"
                          "?service=youtube&hl=en",
                          wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_selector('input[type="email"]', timeout=15_000)
                page.fill('input[type="email"]', email)
                page.keyboard.press("Enter")
                page.wait_for_selector('input[type="password"]', timeout=15_000)
                page.wait_for_timeout(800)
                page.fill('input[type="password"]', password)
                page.keyboard.press("Enter")
                page.wait_for_url("*youtube*", timeout=30_000)
                logger.info("  ✓ Signed in")

                # ── Open YouTube Studio ───────────────────────────────────────
                page.goto("https://studio.youtube.com",
                          wait_until="networkidle", timeout=30_000)
                _screenshot("studio_loaded")

                # ── Click Create → Upload videos ──────────────────────────────
                page.click('button[aria-label="Create"], ytcp-button#create-icon',
                           timeout=15_000)
                page.wait_for_timeout(500)
                page.click('tp-yt-paper-item:has-text("Upload videos")', timeout=10_000)

                # ── Set the file ──────────────────────────────────────────────
                with page.expect_file_chooser() as fc_info:
                    page.click('ytcp-uploads-dialog #select-files-button', timeout=10_000)
                fc_info.value.set_files(str(video_path))
                logger.info("  File set — waiting for upload dialog…")

                # ── Details step: title & description ─────────────────────────
                page.wait_for_selector('ytcp-uploads-details', timeout=60_000)
                # Clear and fill title
                title_box = page.locator('#title-textarea #textbox, ytcp-mention-textbox[label="Title"] #textbox').first
                title_box.click()
                title_box.press("Control+a")
                title_box.type(title, delay=30)

                desc_box = page.locator('#description-textarea #textbox, ytcp-mention-textbox[label="Description"] #textbox').first
                desc_box.click()
                desc_box.type(description[:4900], delay=5)
                _screenshot("details_filled")

                # ── Next → Next → Next (3 wizard steps) ──────────────────────
                for step in range(3):
                    page.click('ytcp-button#next-button', timeout=10_000)
                    page.wait_for_timeout(1_000)

                # ── Visibility: set Public ────────────────────────────────────
                page.wait_for_selector('ytcp-video-visibility-select', timeout=15_000)
                page.click('tp-yt-paper-radio-button[name="PUBLIC"]', timeout=10_000)
                _screenshot("visibility")

                # ── Publish ───────────────────────────────────────────────────
                page.click('ytcp-button#done-button', timeout=10_000)
                page.wait_for_timeout(5_000)
                _screenshot("published")

                # Try to grab the video ID from the post-publish URL or dialog
                current_url = page.url
                m = re.search(r'/video/([A-Za-z0-9_-]{11})', current_url)
                if not m:
                    # Sometimes the ID appears in the page content
                    content = page.content()
                    m = re.search(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', content)
                video_id = m.group(1) if m else "BROWSER_UPLOAD_OK"
                logger.info(f"  ✓ Browser upload complete — video_id={video_id}")
                return video_id

            except PWTimeout as e:
                logger.error(f"Browser upload timed out: {e}")
                _screenshot("timeout_error")
                return None
            except Exception as e:
                logger.error(f"Browser upload failed: {e}", exc_info=True)
                _screenshot("error")
                return None
            finally:
                browser.close()

    def get_video_stats(self, video_ids: list[str]) -> dict[str, int]:
        """
        Return {video_id: view_count} for the given list of YouTube video IDs.

        Requires youtube.readonly scope.  If the stored token predates that
        scope being added, this raises an HttpError 403 — callers should
        catch it and continue without view counts.
        """
        if not video_ids:
            return {}

        result: dict[str, int] = {}
        service = self._get_service()

        # YouTube API accepts up to 50 IDs per request
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i : i + 50]
            try:
                resp = service.videos().list(
                    part="statistics",
                    id=",".join(batch),
                ).execute()
                for item in resp.get("items", []):
                    views = item.get("statistics", {}).get("viewCount", 0)
                    result[item["id"]] = int(views)
            except HttpError as e:
                if e.resp.status == 403:
                    raise   # let caller handle scope error
                logger.warning(f"videos.list API error for batch: {e}")

        return result

    def test_auth(self) -> bool:
        """Verify credentials work by fetching the channel list."""
        try:
            service = self._get_service()
            resp = service.channels().list(part="snippet", mine=True).execute()
            items = resp.get("items", [])
            if items:
                name = items[0]["snippet"]["title"]
                logger.info(f"Authenticated as channel: {name!r}")
                return True
            logger.warning("Authentication succeeded but no channel found")
            return False
        except Exception as e:
            logger.error(f"Auth test failed: {e}")
            return False
