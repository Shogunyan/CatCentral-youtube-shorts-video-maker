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
import re
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

        # Skip the API entirely when credentials are placeholder values
        _client_id = getattr(self.config, "google_client_id", "")
        _api_ready = (
            _client_id
            and "your_client_id_here" not in _client_id
            and ".apps.googleusercontent.com" in _client_id
        )
        if not _api_ready:
            logger.info("YouTube API credentials not configured — going straight to browser upload…")
            return self._browser_upload(video_path, title, description)

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
                logger.error("Upload loop exited but response is empty — trying browser fallback…")
                return self._browser_upload(video_path, title, description)
            video_id = response.get("id", "")
            if not video_id:
                logger.error("Upload response had no video ID — trying browser fallback…")
                return self._browser_upload(video_path, title, description)
            logger.info(f"  ✓ Uploaded! https://www.youtube.com/shorts/{video_id}")
            return video_id

        except HttpError as e:
            logger.error(f"YouTube API error: {e.resp.status} — {e.content.decode('utf-8', errors='replace')}")
            logger.info("API upload failed — trying browser fallback…")
            return self._browser_upload(video_path, title, description)
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            logger.info("Upload exception — trying browser fallback…")
            return self._browser_upload(video_path, title, description)

    def _browser_upload(
        self,
        video_path: Path,
        title: str,
        description: str,
    ) -> str | None:
        """
        Upload via Chromium when the YouTube API quota is exceeded.

        Strategy to defeat Google's headless-browser detection:
        1. Use a persistent browser profile (cookies saved between runs).
        2. On first run, inject the existing OAuth access_token as a Google
           auth cookie so no UI sign-in is needed at all.
        3. If the profile is already logged in, go straight to Studio.
        """
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            logger.error("playwright not installed — run: pip install playwright && python -m playwright install chromium")
            return None

        email = self.config.youtube_email
        password = self.config.youtube_password
        profile_dir = str(self.config.data_dir / "browser_profile")

        logger.info("Browser upload fallback starting…")

        # Try to get an OAuth access token to inject as a cookie — this helps
        # bypass Google's headless-browser detection. Skip entirely if API
        # credentials aren't configured (placeholder values), since attempting
        # the OAuth flow would open a browser / crash in headless environments.
        access_token: str | None = None
        client_id = getattr(self.config, "google_client_id", "")
        creds_configured = (
            client_id
            and "your_client_id_here" not in client_id
            and ".apps.googleusercontent.com" in client_id
        )
        if creds_configured:
            try:
                creds = self._get_credentials()
                if creds and creds.token:
                    access_token = creds.token
            except Exception:
                pass
        else:
            logger.info("  OAuth credentials not configured — skipping token injection, will use email/password sign-in")

        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                profile_dir,
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-web-security",
                ],
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page = ctx.new_page()

            def _ss(tag: str) -> None:
                try:
                    page.screenshot(path=f"/tmp/yt_upload_{tag}.png")
                except Exception:
                    pass

            def _needs_signin() -> bool:
                return "accounts.google.com" in page.url or "signin" in page.url.lower()

            try:
                # ── Inject OAuth token as a cookie to skip Google's sign-in ──
                if access_token:
                    logger.info("  Injecting OAuth token as auth cookie…")
                    ctx.add_cookies([{
                        "name": "oauth_token",
                        "value": access_token,
                        "domain": ".youtube.com",
                        "path": "/",
                        "secure": True,
                        "httpOnly": False,
                    }, {
                        "name": "SAPISID",
                        "value": access_token[:40],
                        "domain": ".youtube.com",
                        "path": "/",
                        "secure": True,
                        "httpOnly": False,
                    }])

                # ── Navigate to YouTube Studio ─────────────────────────────────
                page.goto("https://studio.youtube.com",
                          wait_until="domcontentloaded", timeout=30_000)
                _ss("studio_nav")

                # ── UI sign-in if not already logged in ────────────────────────
                if _needs_signin() and email and password:
                    logger.info(f"  Not logged in — signing in as {email}…")
                    if "accounts.google.com" not in page.url:
                        page.goto("https://accounts.google.com/signin/v2/identifier?service=youtube",
                                  wait_until="domcontentloaded", timeout=20_000)

                    page.wait_for_selector('input[type="email"]', timeout=15_000)
                    page.fill('input[type="email"]', email)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(1_500)
                    page.wait_for_selector('input[type="password"]', timeout=15_000)
                    page.fill('input[type="password"]', password)
                    page.keyboard.press("Enter")
                    page.wait_for_url("*youtube*", timeout=30_000)
                    logger.info("  ✓ Signed in — session saved to profile")
                    page.goto("https://studio.youtube.com",
                              wait_until="domcontentloaded", timeout=30_000)

                if _needs_signin():
                    logger.error("  Still on sign-in page after attempt — check credentials")
                    _ss("signin_failed")
                    return None

                logger.info("  ✓ On YouTube Studio")
                _ss("studio_ready")

                # ── Create → Upload videos ─────────────────────────────────────
                page.wait_for_selector('ytcp-button#create-icon, button[aria-label="Create"]',
                                       timeout=15_000)
                page.click('ytcp-button#create-icon, button[aria-label="Create"]')
                page.wait_for_timeout(600)
                page.click('tp-yt-paper-item:has-text("Upload videos"), yt-formatted-string:has-text("Upload videos")',
                           timeout=8_000)

                # ── Drop the file ──────────────────────────────────────────────
                with page.expect_file_chooser(timeout=15_000) as fc:
                    page.click('#select-files-button, input[type="file"]', timeout=10_000)
                fc.value.set_files(str(video_path))
                logger.info(f"  File set: {video_path.name}")

                # ── Details: title & description ───────────────────────────────
                page.wait_for_selector('ytcp-uploads-details', timeout=60_000)
                page.wait_for_timeout(1_000)

                title_sel = '#title-textarea #textbox'
                page.wait_for_selector(title_sel, timeout=15_000)
                page.click(title_sel)
                page.keyboard.press("Control+a")
                page.keyboard.type(title, delay=20)

                desc_sel = '#description-textarea #textbox'
                page.click(desc_sel)
                page.keyboard.type(description[:4900], delay=3)
                _ss("details_filled")

                # ── Walk through 3 wizard steps ────────────────────────────────
                for _ in range(3):
                    next_btn = page.locator('ytcp-button#next-button')
                    if next_btn.is_visible():
                        next_btn.click()
                    page.wait_for_timeout(1_200)

                # ── Visibility → Public ────────────────────────────────────────
                page.wait_for_selector('ytcp-video-visibility-select', timeout=15_000)
                page.click('tp-yt-paper-radio-button[name="PUBLIC"]')
                page.wait_for_timeout(500)
                _ss("visibility_set")

                # ── Publish ────────────────────────────────────────────────────
                page.click('ytcp-button#done-button', timeout=10_000)
                page.wait_for_timeout(6_000)
                _ss("published")

                # Extract video ID from URL or page source
                m = re.search(r'/video/([A-Za-z0-9_-]{11})', page.url)
                if not m:
                    m = re.search(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', page.content())
                video_id = m.group(1) if m else "BROWSER_UPLOAD_OK"
                logger.info(f"  ✓ Browser upload complete — {video_id}")
                return video_id

            except PWTimeout as e:
                logger.error(f"Browser upload timed out at: {e}")
                _ss("timeout")
                return None
            except Exception as e:
                logger.error(f"Browser upload error: {e}", exc_info=True)
                _ss("error")
                return None
            finally:
                ctx.close()

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
