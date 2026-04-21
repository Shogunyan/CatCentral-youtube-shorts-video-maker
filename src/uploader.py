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
        Upload via Chromium using a persistent profile to preserve the session.

        On first run the browser signs in with YOUTUBE_EMAIL / YOUTUBE_PASSWORD
        and the session is saved to data/browser_profile so subsequent runs
        skip sign-in entirely.
        """
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            logger.error("playwright not installed — run: pip install playwright && python -m playwright install chromium")
            return None

        email = self.config.youtube_email
        password = self.config.youtube_password
        if not email or not password:
            logger.error("YOUTUBE_EMAIL / YOUTUBE_PASSWORD not set in .env — cannot do browser upload")
            return None

        profile_dir = str(self.config.data_dir / "browser_profile")
        logger.info("Browser upload starting…")

        # Skip OAuth token injection when API creds are placeholder values —
        # attempting the OAuth flow in headless mode would fail or open a browser.
        access_token: str | None = None
        _client_id = getattr(self.config, "google_client_id", "")
        if _client_id and "your_client_id_here" not in _client_id and ".apps.googleusercontent.com" in _client_id:
            try:
                creds = self._get_credentials()
                if creds and creds.token:
                    access_token = creds.token
            except Exception:
                pass
        else:
            logger.info("  API creds not set — will sign in via email/password")

        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                profile_dir,
                headless=True,
                ignore_https_errors=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--disable-web-security",
                    "--window-size=1280,800",
                ],
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            page = ctx.new_page()
            # Hide the webdriver flag so Google's bot detection doesn't fire
            page.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )

            def _ss(tag: str) -> None:
                try:
                    page.screenshot(path=f"/tmp/yt_upload_{tag}.png")
                    logger.debug(f"  Screenshot: /tmp/yt_upload_{tag}.png")
                except Exception:
                    pass

            def _needs_signin() -> bool:
                u = page.url
                return (
                    "accounts.google.com" in u
                    or "signin" in u.lower()
                    or "ServiceLogin" in u
                )

            try:
                # ── Inject OAuth cookie to skip sign-in (best effort) ──────────
                if access_token:
                    logger.info("  Injecting OAuth token cookie…")
                    ctx.add_cookies([
                        {"name": "oauth_token", "value": access_token,
                         "domain": ".youtube.com", "path": "/", "secure": True},
                        {"name": "SAPISID", "value": access_token[:40],
                         "domain": ".youtube.com", "path": "/", "secure": True},
                    ])

                # ── Go to Studio ───────────────────────────────────────────────
                page.goto("https://studio.youtube.com",
                          wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(2_000)
                _ss("01_studio_nav")
                logger.info(f"  After nav → {page.url[:80]}")

                # ── Sign in if redirected to Google login ──────────────────────
                if _needs_signin():
                    logger.info(f"  Not logged in — signing in as {email}…")
                    if "accounts.google.com" not in page.url:
                        page.goto(
                            "https://accounts.google.com/signin/v2/identifier"
                            "?service=youtube&hl=en",
                            wait_until="domcontentloaded", timeout=20_000,
                        )
                    _ss("02_signin_page")

                    # Email field — Google uses #identifierId
                    email_sel = '#identifierId, input[type="email"]'
                    page.wait_for_selector(email_sel, timeout=15_000)
                    page.fill(email_sel, email)
                    _ss("03_email_filled")
                    page.click('#identifierNext, button:has-text("Next")')
                    page.wait_for_timeout(2_000)
                    _ss("04_after_email_next")

                    # Password field
                    pwd_sel = 'input[type="password"]:visible'
                    page.wait_for_selector(pwd_sel, timeout=15_000)
                    page.fill(pwd_sel, password)
                    _ss("05_pwd_filled")
                    page.click('#passwordNext, button:has-text("Next"), button:has-text("Sign in")')

                    # Wait for redirect out of accounts.google.com
                    try:
                        page.wait_for_url("*youtube*", timeout=30_000)
                    except PWTimeout:
                        _ss("06_signin_redirect_timeout")
                        logger.error(
                            f"  Sign-in redirect timed out — current URL: {page.url[:120]}\n"
                            "  Possible causes: wrong password, 2FA, or Google challenge."
                        )
                        return None

                    logger.info("  ✓ Signed in — session saved to profile")
                    _ss("07_post_signin")
                    page.goto("https://studio.youtube.com",
                              wait_until="domcontentloaded", timeout=30_000)
                    page.wait_for_timeout(3_000)
                    _ss("08_studio_after_signin")

                if _needs_signin():
                    logger.error(f"  Still on sign-in page: {page.url[:120]}")
                    _ss("09_signin_failed")
                    return None

                logger.info(f"  ✓ On YouTube Studio: {page.url[:80]}")
                _ss("10_studio_ready")

                # ── Create → Upload videos ─────────────────────────────────────
                create_sel = (
                    'ytcp-button#create-icon, '
                    'button[aria-label="Create"], '
                    '#create-icon'
                )
                page.wait_for_selector(create_sel, timeout=20_000)
                page.click(create_sel)
                page.wait_for_timeout(1_200)
                _ss("11_create_clicked")

                upload_sel = (
                    'tp-yt-paper-item:has-text("Upload videos"), '
                    'yt-formatted-string:has-text("Upload videos"), '
                    'a:has-text("Upload videos")'
                )
                page.wait_for_selector(upload_sel, timeout=15_000)
                page.click(upload_sel)
                page.wait_for_timeout(1_500)
                _ss("11b_upload_clicked")

                # ── Verify upload file-select dialog opened ────────────────────
                dialog_ready_sel = (
                    '#select-files-button, '
                    'ytcp-file-upload, '
                    'ytcp-upload-dialog, '
                    '#upload-dialog'
                )
                try:
                    page.wait_for_selector(dialog_ready_sel, timeout=15_000)
                    logger.info("  Upload dialog open")
                except PWTimeout:
                    _ss("12_no_dialog")
                    logger.error(
                        f"  Upload file-select dialog never opened — "
                        f"URL: {page.url[:120]}"
                    )
                    return None
                _ss("12_upload_dialog")

                # ── Select the file via file-chooser (triggers YouTube's upload) ─
                try:
                    with page.expect_file_chooser(timeout=15_000) as fc:
                        page.click('#select-files-button', timeout=10_000)
                    fc.value.set_files(str(video_path))
                    logger.info(f"  File set via chooser: {video_path.name}")
                except Exception as chooser_err:
                    logger.warning(f"  File chooser failed ({chooser_err}) — trying direct input")
                    page.locator('input[type="file"]').first.set_input_files(str(video_path))
                    logger.info(f"  File set via input: {video_path.name}")
                _ss("13_file_set")
                page.wait_for_timeout(3_000)
                _ss("13b_processing")

                # ── Wait for details panel (try each selector in order) ────────
                logger.info("  Waiting for upload details panel…")
                DETAIL_SELS = [
                    ('ytcp-uploads-details',         90_000),
                    ('ytcp-video-metadata-editor',   20_000),
                    ('#title-textarea',              20_000),
                    ('ytcp-form-input-container',    20_000),
                ]
                details_appeared = False
                for idx, (sel, tmo) in enumerate(DETAIL_SELS):
                    try:
                        page.wait_for_selector(sel, state='attached', timeout=tmo)
                        logger.info(f"  Details panel found: {sel}")
                        details_appeared = True
                        break
                    except PWTimeout:
                        _ss(f"14_try{idx}_timeout")
                        continue

                if not details_appeared:
                    _ss("14_details_never_appeared")
                    logger.error(
                        f"  Upload details panel never appeared — URL: {page.url[:120]}"
                    )
                    return None
                page.wait_for_timeout(2_000)
                _ss("14_details_form")

                # ── Fill title ─────────────────────────────────────────────────
                title_filled = False
                for t_sel in [
                    '#title-textarea #textbox',
                    '#title-textarea ytcp-ve #textbox',
                    'ytcp-form-input-container[id="title-textarea"] #textbox',
                ]:
                    try:
                        el = page.locator(t_sel).first
                        el.wait_for(state='visible', timeout=8_000)
                        # fill() sets text directly without typing char-by-char
                        try:
                            el.fill(title)
                        except Exception:
                            el.click()
                            page.keyboard.press("Control+a")
                            page.keyboard.type(title, delay=10)
                        title_filled = True
                        logger.info("  Title filled")
                        break
                    except PWTimeout:
                        continue
                if not title_filled:
                    logger.warning("  Title field not found — uploading without title change")

                # ── Fill description ───────────────────────────────────────────
                for d_sel in [
                    '#description-textarea #textbox',
                    '#description-textarea ytcp-ve #textbox',
                    'ytcp-form-input-container[id="description-textarea"] #textbox',
                ]:
                    try:
                        el = page.locator(d_sel).first
                        el.wait_for(state='visible', timeout=8_000)
                        try:
                            el.fill(description[:4900])
                        except Exception:
                            el.click()
                            page.keyboard.type(description[:500], delay=1)
                        logger.info("  Description filled")
                        break
                    except PWTimeout:
                        continue

                _ss("15_details_filled")

                # ── Walk wizard — stop the moment visibility page appears ───────
                # After each Next click we wait for EITHER another Next button
                # OR the visibility page (up to 10 s). This handles pages that
                # take a few seconds to transition.
                vis_sel = (
                    'ytcp-video-visibility-select, '
                    'ytcp-uploads-publish, '
                    '#visibility-select, '
                    'tp-yt-paper-radio-button[name="PUBLIC"]'
                )
                combined_sel = f'ytcp-button#next-button, {vis_sel}'

                for step in range(6):
                    # Already on visibility page?
                    if page.locator(vis_sel).count() > 0:
                        logger.info(f"  On visibility page after {step} step(s)")
                        break

                    # Wait up to 10 s for either Next or visibility to appear
                    try:
                        page.wait_for_selector(combined_sel, timeout=10_000)
                    except PWTimeout:
                        _ss(f"wizard_step{step}_stalled")
                        logger.warning(f"  Step {step}: page not ready after 10s — stopping wizard")
                        break

                    # Re-check after wait — visibility may have loaded
                    if page.locator(vis_sel).count() > 0:
                        logger.info(f"  On visibility page after {step} step(s)")
                        break

                    # Click Next
                    next_btn = page.locator('ytcp-button#next-button').first
                    try:
                        if next_btn.get_attribute('disabled') is not None:
                            page.wait_for_timeout(1_000)
                            continue
                        next_btn.click()
                        logger.info(f"  Wizard step {step + 1}: Next clicked")
                    except Exception as ne:
                        logger.warning(f"  Step {step}: Next click failed ({ne}) — stopping")
                        break

                _ss("16_after_wizard")

                # ── Ensure we landed on the visibility page ────────────────────
                try:
                    page.wait_for_selector(vis_sel, timeout=15_000)
                    _ss("16_visibility_page")
                except PWTimeout:
                    _ss("16_no_visibility_page")
                    logger.error(f"  Visibility page never appeared — URL: {page.url[:120]}")
                    return None

                # ── Set visibility to Public ───────────────────────────────────
                for pub_sel in [
                    'tp-yt-paper-radio-button[name="PUBLIC"]',
                    'ytcp-radio-button[value="PUBLIC"]',
                    'paper-radio-button[name="PUBLIC"]',
                ]:
                    try:
                        page.wait_for_selector(pub_sel, timeout=5_000)
                        page.click(pub_sel)
                        logger.info("  Visibility set to Public")
                        break
                    except PWTimeout:
                        continue
                page.wait_for_timeout(800)
                _ss("17_public_set")

                # ── Wait for Done/Publish button to be enabled ─────────────────
                done_btn = page.locator(
                    'ytcp-button#done-button, ytcp-button[id="done-button"]'
                )
                try:
                    done_btn.first.wait_for(state='visible', timeout=30_000)
                except PWTimeout:
                    _ss("18_no_done_button")
                    logger.error("  Done/Publish button never appeared")
                    return None

                # YouTube keeps Done disabled while still processing — poll up to 60 s
                for _ in range(60):
                    if done_btn.first.get_attribute('disabled') is None:
                        break
                    logger.info("  Waiting for upload to finish processing…")
                    page.wait_for_timeout(1_000)

                done_btn.first.click()
                logger.info("  Publish clicked — waiting for confirmation…")
                page.wait_for_timeout(8_000)
                _ss("18_published")
                logger.info(f"  Post-publish URL: {page.url[:120]}")

                # Extract video ID from URL or page source
                m = re.search(r'/video/([A-Za-z0-9_-]{11})', page.url)
                if not m:
                    m = re.search(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', page.content())
                video_id = m.group(1) if m else "BROWSER_UPLOAD_OK"
                logger.info(f"  ✓ Browser upload complete — {video_id}")
                return video_id

            except PWTimeout as e:
                logger.error(
                    f"Browser upload timed out: {e}\n"
                    f"  Current URL: {page.url[:120]}\n"
                    f"  Debug screenshots in /tmp/yt_upload_*.png"
                )
                _ss("timeout")
                return None
            except Exception as e:
                logger.error(
                    f"Browser upload error: {e}\n"
                    f"  Current URL: {page.url[:120]}\n"
                    f"  Debug screenshots in /tmp/yt_upload_*.png",
                    exc_info=True,
                )
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
