"""
tui.py — CatCentral Terminal UI (powered by Textual)

Screens:
  SetupScreen    — First-time credential setup with paste inputs + OAuth flow
  DashboardScreen — Live progress bar, action label, log panel, run/schedule buttons
"""

from __future__ import annotations

import datetime
import logging
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Log,
    ProgressBar,
    Rule,
    Static,
)

# ── Thread-safe messages posted from background workers ───────────────────────

class PipelineProgress(Message):
    """Progress update from the pipeline worker thread."""
    def __init__(self, percent: float, action: str, log_msg: str = "") -> None:
        super().__init__()
        self.percent = percent
        self.action = action
        self.log_msg = log_msg


class PipelineFinished(Message):
    """Pipeline run completed."""
    def __init__(self, success: bool, detail: str = "") -> None:
        super().__init__()
        self.success = success
        self.detail = detail


class AuthStatus(Message):
    """Result of the YouTube OAuth flow."""
    def __init__(self, success: bool, detail: str = "") -> None:
        super().__init__()
        self.success = success
        self.detail = detail


# ── Helpers ───────────────────────────────────────────────────────────────────

def _next_upload_str(upload_times: list[str]) -> str:
    if not upload_times:
        return "No upload times configured"
    now = datetime.datetime.now()
    valid: list[str] = []
    for t in upload_times:
        try:
            parts = t.strip().split(":")
            if len(parts) >= 2:
                int(parts[0]); int(parts[1])
                valid.append(t.strip())
        except (ValueError, IndexError):
            pass
    if not valid:
        return "Upload times invalid — check Settings"
    for t in sorted(valid):
        h, m = int(t.split(":")[0]), int(t.split(":")[1])
        scheduled = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if scheduled > now:
            return f"Next upload: {t}"
    first = sorted(valid)[0]
    return f"Next upload: {first}  (tomorrow)"


def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


# ── Setup Screen ──────────────────────────────────────────────────────────────

class SetupScreen(Screen):
    """Credential input form + YouTube OAuth flow."""

    BINDINGS = [Binding("escape", "go_back", "Back", show=True)]

    def __init__(self, as_settings: bool = False) -> None:
        super().__init__()
        self._as_settings = as_settings  # True when opened from the dashboard

    def action_go_back(self) -> None:
        """Escape / Back — only navigate away if there's a dashboard to return to."""
        if self._as_settings:
            self.app.pop_screen()

    @on(Button.Pressed, "#btn-back")
    def on_back(self) -> None:
        self.app.pop_screen()

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="setup-wrap"):
            if self._as_settings:
                yield Button("← Back to Dashboard", id="btn-back", variant="default")
            yield Static("🐱  CatCentral — Setup", id="setup-title")
            yield Rule()

            yield Static(
                "Paste your Google OAuth credentials below.  "
                "They are only stored locally in your .env file.",
                classes="instructions",
            )
            yield Static("How to get them:", classes="help-header")
            yield Static(
                "  1.  Go to  console.cloud.google.com", classes="help-step"
            )
            yield Static(
                "  2.  Create a project → APIs & Services → Library",
                classes="help-step",
            )
            yield Static(
                "  3.  Enable  YouTube Data API v3", classes="help-step"
            )
            yield Static(
                "  4.  Credentials → Create OAuth Client ID → Desktop App",
                classes="help-step",
            )
            yield Static(
                "  5.  Copy the Client ID and Client Secret from the popup",
                classes="help-step",
            )

            yield Rule()

            yield Static("Google Client ID", classes="field-label")
            yield Input(
                placeholder="123456789-abc…apps.googleusercontent.com",
                id="inp-cid",
            )

            yield Static("Google Client Secret", classes="field-label")
            yield Input(
                placeholder="GOCSPX-…",
                password=True,
                id="inp-csecret",
            )

            yield Rule()

            yield Static(
                "Upload Schedule  (24 h times, comma-separated)",
                classes="field-label",
            )
            yield Input(value="09:00,14:00,19:00", id="inp-times")

            yield Static("Instagram Username  (optional)", classes="field-label")
            yield Input(
                placeholder="leave blank to skip",
                id="inp-ig-user",
            )
            yield Static("Instagram Password  (optional)", classes="field-label")
            yield Input(
                placeholder="leave blank to skip",
                password=True,
                id="inp-ig-pass",
            )

            yield Rule()

            yield Button(
                "▶  Authorize YouTube & Save",
                id="btn-auth",
                variant="success",
            )
            yield Static("", id="setup-status")

        yield Footer()

    # ── Handlers ──────────────────────────────────────────────────────────────

    @on(Button.Pressed, "#btn-auth")
    def on_authorize(self) -> None:
        cid = self.query_one("#inp-cid", Input).value.strip()
        csecret = self.query_one("#inp-csecret", Input).value.strip()
        times = self.query_one("#inp-times", Input).value.strip()
        ig_user = self.query_one("#inp-ig-user", Input).value.strip()
        ig_pass = self.query_one("#inp-ig-pass", Input).value.strip()

        if not cid or not csecret:
            self._set_status("❌  Client ID and Client Secret are required.", error=True)
            return

        btn = self.query_one("#btn-auth", Button)
        btn.disabled = True
        btn.label = "⏳  Browser opening for authorization…"
        self._set_status("🌐  A browser window will open — log in and click Allow.")
        self._do_auth(cid, csecret, times, ig_user, ig_pass)

    @work(thread=True)
    def _do_auth(
        self,
        cid: str, csecret: str,
        times: str, ig_user: str, ig_pass: str,
    ) -> None:
        import threading
        import time
        _URL_FILE = "/tmp/catcentral_auth_url.txt"

        # Clear any stale URL file before starting
        try:
            import os
            if os.path.exists(_URL_FILE):
                os.remove(_URL_FILE)
        except Exception:
            pass

        _auth_done = threading.Event()

        def _copy_to_clipboard(text: str) -> bool:
            """Try to copy text to the system clipboard. Returns True on success."""
            import platform
            import subprocess as _sp
            try:
                sys_name = platform.system()
                if sys_name == "Darwin":
                    _sp.run(["pbcopy"], input=text.encode(), check=True)
                    return True
                # Linux / WSL — try wl-copy, xclip, xsel in order
                for cmd in (
                    ["wl-copy"],
                    ["xclip", "-selection", "clipboard"],
                    ["xsel", "--clipboard", "--input"],
                ):
                    try:
                        _sp.run(cmd, input=text.encode(),
                                check=True, capture_output=True)
                        return True
                    except (FileNotFoundError, Exception):
                        continue
            except Exception:
                pass
            return False

        def _url_watcher() -> None:
            """Poll for the auth URL, copy it to clipboard, and show it in status."""
            import os
            for _ in range(60):          # max ~30s of polling
                if _auth_done.is_set():
                    return
                time.sleep(0.5)
                try:
                    if os.path.exists(_URL_FILE):
                        with open(_URL_FILE) as f:
                            url = f.read().strip()
                        if url:
                            copied = _copy_to_clipboard(url)
                            clip_note = "  (copied to clipboard!)" if copied else ""
                            self.app.call_from_thread(
                                self._set_status,
                                f"🌐  Browser didn't open? Paste this URL in your browser{clip_note}:\n{url}",
                            )
                            return
                except Exception:
                    pass

        watcher = threading.Thread(target=_url_watcher, daemon=True)
        watcher.start()
        try:
            from setup_wizard import _write_env
            _write_env(
                client_id=cid,
                client_secret=csecret,
                instagram_username=ig_user,
                instagram_password=ig_pass,
                upload_times=times,
            )
            from config import Config
            from src.uploader import YouTubeUploader
            cfg = Config()
            uploader = YouTubeUploader(cfg)
            ok = uploader.test_auth()
            self.post_message(
                AuthStatus(success=ok, detail="Channel verified!" if ok else "Auth succeeded but no channel found.")
            )
        except Exception as e:
            self.post_message(AuthStatus(success=False, detail=str(e)))
        finally:
            _auth_done.set()

    @on(AuthStatus)
    def on_auth_result(self, msg: AuthStatus) -> None:
        btn = self.query_one("#btn-auth", Button)
        if msg.success:
            btn.label = "✅  Authorized!"
            self._set_status(f"✅  {msg.detail}  Launching dashboard…")
            self.set_timer(1.5, self._go_dashboard)
        else:
            btn.disabled = False
            btn.label = "▶  Authorize YouTube & Save"
            self._set_status(f"❌  {msg.detail}", error=True)

    def _set_status(self, text: str, error: bool = False) -> None:
        w = self.query_one("#setup-status", Static)
        w.update(text)
        w.set_class(error, "status-error")
        w.set_class(not error, "status-ok")

    def _go_dashboard(self) -> None:
        self.app.pop_screen()
        # Only push a new DashboardScreen on first-run; if one already exists
        # below (user opened Settings from the dashboard), don't stack another.
        if not isinstance(self.app.screen, DashboardScreen):
            self.app.push_screen(DashboardScreen())


# ── Dashboard Screen ──────────────────────────────────────────────────────────

class DashboardScreen(Screen):
    """Main control panel: action status, progress bar, live log, run/schedule buttons."""

    BINDINGS = [
        Binding("r", "run_now", "Run Now"),
        Binding("s", "toggle_schedule", "Scheduler"),
        Binding("t", "open_settings", "Settings"),
        Binding("q", "app.quit", "Quit"),
    ]

    _running: reactive[bool] = reactive(False)
    _sched_active: reactive[bool] = reactive(False)

    def _is_running(self) -> bool:
        return self.__dict__.get("_pipeline_running", False)

    def _set_running(self, val: bool) -> None:
        self.__dict__["_pipeline_running"] = val

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="dash"):
            yield Static("🐱  CatCentral — YouTube Shorts Maker", id="dash-title")
            yield Rule()

            # ── Current action ─────────────────────────────────────────────
            with Container(id="action-box"):
                yield Static(
                    "◉  Idle — press  R  or click  Run Now  to start",
                    id="action-label",
                )

            # ── Progress bar ───────────────────────────────────────────────
            yield ProgressBar(total=100, id="pbar", show_eta=False)

            yield Rule()

            # ── Log ────────────────────────────────────────────────────────
            yield Static("◈  Live Log", id="log-heading")
            yield Log(id="log", max_lines=500, highlight=True)

            yield Rule()

            # ── Button row ─────────────────────────────────────────────────
            with Horizontal(id="btn-row"):
                yield Button("▶  Run Now", id="btn-run", variant="primary")
                yield Button("📅  Start Scheduler", id="btn-sched")
                yield Button("⚙  Settings", id="btn-settings")
                yield Static("", id="sched-info")

        yield Footer()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        from config import Config
        self._cfg = Config()
        self._refresh_sched_label()
        self._log("CatCentral ready.  Press  R  or click  Run Now  to start.")
        self._check_tracker_on_mount()

    @work(thread=True)
    def _check_tracker_on_mount(self) -> None:
        """Background check: show any pending re-upload / milestone notifications."""
        try:
            from src.video_tracker import VideoTracker
            tracker = VideoTracker(self._cfg)
            notes = tracker.get_startup_notifications()
            for note in notes:
                self.app.call_from_thread(self._log, note)
        except Exception:
            pass   # never crash the dashboard over a tracker notification

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_run_now(self) -> None:
        if not self._is_running():
            self._start_pipeline()

    def action_toggle_schedule(self) -> None:
        self._toggle_scheduler()

    def action_open_settings(self) -> None:
        self.app.push_screen(SetupScreen(as_settings=True))

    # ── Button handlers ───────────────────────────────────────────────────────

    @on(Button.Pressed, "#btn-run")
    def _on_run(self) -> None:
        self._start_pipeline()

    @on(Button.Pressed, "#btn-sched")
    def _on_sched(self) -> None:
        self._toggle_scheduler()

    @on(Button.Pressed, "#btn-settings")
    def _on_settings(self) -> None:
        self.app.push_screen(SetupScreen(as_settings=True))

    # ── Pipeline ──────────────────────────────────────────────────────────────

    def _start_pipeline(self) -> None:
        if self._is_running():
            return
        try:
            self._set_running(True)
            btn = self.query_one("#btn-run", Button)
            btn.disabled = True
            btn.label = "⏳  Running…"
            self.query_one("#pbar", ProgressBar).update(progress=0)
            self._update_action("🚀  Starting pipeline…")
            self._log("─" * 48)
            self._log(f"Pipeline started at {_ts()}")
            self._pipeline_worker()
        except Exception as e:
            self._set_running(False)
            self._log(f"❌ Startup error: {e}")

    @work(thread=True)
    def _pipeline_worker(self) -> None:
        import traceback

        def _log_safe(msg):
            try:
                self.app.call_from_thread(self._log, msg)
            except Exception:
                pass

        def _progress(percent, action, log_msg=""):
            try:
                self.app.call_from_thread(self._on_progress_direct, percent, action, log_msg)
            except Exception as e:
                _log_safe(f"Progress error: {e}")

        def _finish(success, detail=""):
            try:
                self.app.call_from_thread(self._on_finished_direct, success, detail)
            except Exception as e:
                _log_safe(f"Finish error: {e}")

        _log_safe("Worker thread started...")

        try:
            from src.scheduler import Pipeline
            _log_safe("Pipeline imported OK")
        except Exception as exc:
            _log_safe(f"IMPORT FAILED: {exc}\n{traceback.format_exc()}")
            _finish(False, f"Import error: {exc}")
            return

        try:
            _log_safe("Creating pipeline...")
            pipeline = Pipeline(self._cfg, reporter=_progress)
            _log_safe("Running pipeline...")
            ok = pipeline.run()
            _log_safe(f"Pipeline finished: success={ok}")
            _finish(ok)
        except Exception as exc:
            _log_safe(f"PIPELINE CRASHED: {exc}\n{traceback.format_exc()}")
            _finish(False, f"{exc}")
        finally:
            # Hard guarantee: always reset even if _finish failed
            self._set_running(False)

    def _on_progress_direct(self, percent: float, action: str, log_msg: str = "") -> None:
        """Called from worker thread via call_from_thread."""
        self.query_one("#pbar", ProgressBar).update(progress=percent)
        self._update_action(action)
        if log_msg:
            self._log(log_msg)

    def _on_finished_direct(self, success: bool, detail: str = "") -> None:
        """Called from worker thread via call_from_thread."""
        self._set_running(False)
        btn = self.query_one("#btn-run", Button)
        btn.disabled = False
        btn.label = "▶  Run Now"
        self._refresh_sched_label()

        if success:
            self.query_one("#pbar", ProgressBar).update(progress=100)
            self._update_action("✅  Done!  Video is live on YouTube.")
            self._log("─" * 48)
            self._log("✅  Upload complete!")
            self._log("─" * 48)
        else:
            self._update_action("❌  Pipeline failed — see log for details")
            self._log(f"❌  Error: {detail}" if detail else "❌  Pipeline failed")

    # ── Scheduler ─────────────────────────────────────────────────────────────

    def _toggle_scheduler(self) -> None:
        btn = self.query_one("#btn-sched", Button)
        if self._sched_active:
            self._sched_active = False
            btn.label = "📅  Start Scheduler"
            self._log("⏹  Scheduler stopped.")
        else:
            self._sched_active = True
            btn.label = "⏹  Stop Scheduler"
            times_str = ", ".join(self._cfg.upload_times)
            self._log(f"📅  Scheduler active.  Upload times: {times_str}")
            self._refresh_sched_label()
            self._sched_loop()

    @work(thread=True)
    def _sched_loop(self) -> None:
        import time
        import schedule as sch

        sch.clear("catcentral")
        for t in self._cfg.upload_times:
            sch.every().day.at(t).do(
                lambda: self.app.call_from_thread(self._start_pipeline)
            ).tag("catcentral")

        while self._sched_active:
            sch.run_pending()
            time.sleep(30)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        self.query_one(Log).write_line(f"[{_ts()}]  {msg}")

    def _update_action(self, text: str) -> None:
        self.query_one("#action-label", Static).update(text)

    def _refresh_sched_label(self) -> None:
        try:
            nxt = _next_upload_str(self._cfg.upload_times)
            self.query_one("#sched-info", Static).update(f"  {nxt}")
        except Exception:
            pass


# ── App ───────────────────────────────────────────────────────────────────────

class CatCentralApp(App):
    TITLE = "CatCentral"
    SUB_TITLE = "YouTube Shorts Maker"

    CSS = """
/* ── Global ──────────────────────────────────────────────────────────── */

Screen {
    background: #0d1117;
    color: #e6edf3;
}

Header {
    background: #161b22;
    color: #58a6ff;
}

Footer {
    background: #161b22;
    color: #8b949e;
}

Rule {
    color: #21262d;
    margin: 1 0;
}

/* ── Setup screen ────────────────────────────────────────────────────── */

#setup-wrap {
    width: 72;
    height: auto;
    margin: 1 4;
    padding: 1 3;
    border: round #30363d;
    background: #161b22;
}

#setup-title {
    text-align: center;
    color: #58a6ff;
    text-style: bold;
    padding: 1 0;
}

.instructions {
    color: #e6edf3;
    margin: 0 0 0 0;
}

.help-header {
    color: #f0883e;
    text-style: bold;
    margin: 1 0 0 0;
}

.help-step {
    color: #8b949e;
}

.field-label {
    color: #8b949e;
    margin: 1 0 0 0;
}

Input {
    background: #010409;
    border: tall #30363d;
    color: #e6edf3;
    margin: 0 0 0 0;
}

Input:focus {
    border: tall #58a6ff;
}

#setup-status {
    text-align: center;
    height: auto;
    min-height: 2;
    margin: 1 0;
    padding: 0;
    overflow: auto;
}

.status-ok {
    color: #3fb950;
}

.status-error {
    color: #f85149;
}

Button#btn-back {
    width: 100%;
    margin: 0 0 1 0;
    background: #21262d;
    color: #8b949e;
    border: tall #30363d;
}

Button#btn-auth {
    width: 100%;
    margin: 1 0 0 0;
}

/* ── Dashboard ───────────────────────────────────────────────────────── */

#dash {
    padding: 0 2;
}

#dash-title {
    text-align: center;
    color: #58a6ff;
    text-style: bold;
    padding: 1 0;
}

#action-box {
    border: round #58a6ff;
    background: #0d2044;
    padding: 1 2;
    height: 5;
    margin: 0 0 1 0;
    content-align: center middle;
}

#action-label {
    color: #79c0ff;
    text-style: bold;
    text-align: center;
    content-align: center middle;
    width: 100%;
}

#pbar {
    margin: 0 0 0 0;
}

ProgressBar > .bar--bar {
    color: #1f6feb;
}

ProgressBar > .bar--complete {
    color: #238636;
}

#log-heading {
    color: #8b949e;
    text-style: bold;
    margin: 0 0 0 0;
}

Log {
    height: 14;
    border: round #30363d;
    background: #010409;
    color: #7ee787;
    margin: 0 0 0 0;
}

#btn-row {
    height: 3;
    align: left middle;
}

Button#btn-run {
    background: #1f6feb;
    color: white;
    margin: 0 1 0 0;
}

Button#btn-run:disabled {
    background: #21262d;
    color: #484f58;
}

Button#btn-sched {
    background: #21262d;
    color: #e6edf3;
    border: tall #30363d;
    margin: 0 1 0 0;
}

Button#btn-settings {
    background: #21262d;
    color: #8b949e;
    border: tall #30363d;
}

#sched-info {
    color: #8b949e;
    content-align: left middle;
    padding: 0 1;
}
"""

    def on_mount(self) -> None:
        try:
            from config import Config
            cfg = Config()
            issues = cfg.validate()
            if issues or not cfg.token_path.exists():
                self.push_screen(SetupScreen())
            else:
                self.push_screen(DashboardScreen())
        except Exception:
            self.push_screen(SetupScreen())


# ── Entry point ───────────────────────────────────────────────────────────────

def run() -> None:
    CatCentralApp().run()


if __name__ == "__main__":
    run()
