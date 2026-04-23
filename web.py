"""
web.py — CatCentral browser interface.

Run:  python3 web.py
Then open http://YOUR-LOCAL-IP:5000 on any device on the same network.
"""
import logging
import os
import queue
import re
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, Response, redirect, render_template_string, request, session, url_for
from dotenv import set_key, dotenv_values

app = Flask(__name__)
app.secret_key = os.urandom(24)

ENV_PATH = Path(__file__).parent / ".env"

_lock = threading.Lock()
_running = False
_log_queue: queue.Queue = queue.Queue()

# ── Shared styles ──────────────────────────────────────────────────────────────

_BASE_STYLE = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #0d0d0d; color: #e0e0e0;
  font-family: 'Segoe UI', system-ui, sans-serif;
  min-height: 100vh;
}
a { color: #ff4444; text-decoration: none; }
a:hover { text-decoration: underline; }
input, select {
  background: #1a1a1a; color: #e0e0e0;
  border: 1px solid #333; border-radius: 8px;
  padding: 10px 14px; font-size: 0.95rem; width: 100%;
  outline: none; transition: border-color 0.2s;
}
input:focus, select:focus { border-color: #ff4444; }
input[type="password"] { letter-spacing: 2px; }
input[type="password"]::placeholder { letter-spacing: normal; }
label { display: block; font-size: 0.82rem; color: #888; margin-bottom: 6px; }
.field { margin-bottom: 18px; }
.btn {
  background: #ff4444; color: #fff; border: none;
  border-radius: 10px; padding: 12px 32px;
  font-size: 1rem; font-weight: 700; cursor: pointer;
  transition: background 0.2s, transform 0.1s;
}
.btn:hover { background: #ff2222; transform: scale(1.02); }
.btn:active { transform: scale(0.98); }
.btn-ghost {
  background: transparent; color: #888;
  border: 1px solid #333; border-radius: 10px;
  padding: 10px 24px; font-size: 0.9rem;
  font-weight: 600; cursor: pointer;
  transition: border-color 0.2s, color 0.2s;
}
.btn-ghost:hover { border-color: #888; color: #e0e0e0; }
.card {
  background: #111; border: 1px solid #1e1e1e;
  border-radius: 14px; padding: 32px;
}
.section-title {
  font-size: 0.7rem; font-weight: 700; letter-spacing: 2px;
  color: #555; text-transform: uppercase; margin-bottom: 16px;
}
.badge {
  display: inline-block; padding: 2px 10px;
  border-radius: 99px; font-size: 0.75rem; font-weight: 600;
}
.badge-ok  { background: #1a3a1a; color: #39ff14; }
.badge-err { background: #3a1a1a; color: #ff4444; }
nav {
  display: flex; align-items: center; justify-content: space-between;
  padding: 18px 32px; border-bottom: 1px solid #1a1a1a;
}
nav .logo { font-size: 1.2rem; font-weight: 800; color: #fff; letter-spacing: 1px; }
nav .nav-links { display: flex; gap: 16px; }
.container { max-width: 860px; margin: 0 auto; padding: 40px 20px; }
.flash { background: #3a1a1a; color: #ff8888; border-radius: 8px;
         padding: 12px 16px; margin-bottom: 24px; font-size: 0.9rem; }
.flash.ok { background: #1a3a1a; color: #88ff88; }
"""

# ── Nav ────────────────────────────────────────────────────────────────────────

def _nav(active=""):
    return f"""
<nav>
  <span class="logo">🐱 CatCentral</span>
  <div class="nav-links">
    <a href="/" style="{'color:#fff;font-weight:700' if active=='home' else 'color:#888'}">Run</a>
    <a href="/settings" style="{'color:#fff;font-weight:700' if active=='settings' else 'color:#888'}">Settings</a>
  </div>
</nav>"""

# ── Helpers ────────────────────────────────────────────────────────────────────

def _env_vals():
    return dotenv_values(str(ENV_PATH)) if ENV_PATH.exists() else {}

def _save_env(updates: dict):
    ENV_PATH.touch(exist_ok=True)
    for k, v in updates.items():
        if v is not None:
            set_key(str(ENV_PATH), k, v)

def _is_configured():
    v = _env_vals()
    return bool(v.get("YOUTUBE_EMAIL") and v.get("YOUTUBE_PASSWORD"))

# ── Home / run page ────────────────────────────────────────────────────────────

HOME = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CatCentral</title>
  <style>{{ style }}</style>
</head>
<body>
{{ nav }}
<div class="container">
  {% if flash %}<div class="flash {{ flash_cls }}">{{ flash }}</div>{% endif %}

  <div style="text-align:center;padding:48px 0 40px">
    <div style="font-size:3.5rem;margin-bottom:12px">🐱</div>
    <h1 style="font-size:2rem;color:#fff;margin-bottom:8px">CatCentral</h1>
    <p style="color:#555;font-size:0.95rem">Upload a ranked cat Short to YouTube in one click</p>
  </div>

  <div style="text-align:center;margin-bottom:40px">
    <button id="run-btn" class="btn" style="padding:20px 64px;font-size:1.4rem;border-radius:14px" onclick="runPipeline()">
      ▶ &nbsp;RUN PIPELINE
    </button>
    <p style="color:#444;font-size:0.8rem;margin-top:10px">or press <kbd style="background:#1a1a1a;padding:2px 8px;border-radius:4px;border:1px solid #333">R</kbd></p>
    <p id="status" style="color:#888;font-size:0.85rem;margin-top:8px;height:20px"></p>
  </div>

  <div class="card">
    <div class="section-title">Live Log</div>
    <div id="log-box" style="font-family:'Courier New',monospace;font-size:0.8rem;line-height:1.7;min-height:80px;max-height:55vh;overflow-y:auto;white-space:pre-wrap;word-break:break-all"></div>
  </div>
</div>

<script>
let running = false, es = null;
document.addEventListener('keydown', e => { if ((e.key==='r'||e.key==='R') && !e.ctrlKey && !e.metaKey) runPipeline(); });

function setRunning(v) {
  running = v;
  document.getElementById('run-btn').disabled = v;
  document.getElementById('status').textContent = v ? 'Pipeline running…' : '';
}
function cls(t) {
  t = t.toLowerCase();
  if (t.includes('error')||t.includes('failed')||t.includes('✗')) return '#ff4444';
  if (t.includes('warn')) return '#ffaa00';
  if (t.includes('✓')||t.includes('done')||t.includes('complete')||t.includes('published')||t.includes('live')) return '#39ff14';
  if (t.includes('retrying')||t.includes('waiting')) return '#ffaa00';
  return '#b0b0b0';
}
function appendLog(text) {
  const box = document.getElementById('log-box');
  const line = document.createElement('div');
  line.style.color = cls(text);
  line.textContent = text;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}
function runPipeline() {
  if (running) return;
  setRunning(true);
  document.getElementById('log-box').innerHTML = '';
  if (es) { es.close(); es = null; }
  fetch('/run', {method:'POST'}).then(r=>r.json()).then(data => {
    if (!data.ok) { appendLog(data.error||'Could not start pipeline'); setRunning(false); return; }
    es = new EventSource('/stream');
    es.onmessage = e => {
      if (e.data === '__DONE__') { es.close(); es=null; setRunning(false); document.getElementById('status').textContent='Finished!'; }
      else appendLog(e.data);
    };
    es.onerror = () => { appendLog('Connection lost.'); es.close(); es=null; setRunning(false); };
  }).catch(err => { appendLog('Request failed: '+err); setRunning(false); });
}
</script>
</body>
</html>"""

# ── Settings page ──────────────────────────────────────────────────────────────

SETTINGS = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Settings — CatCentral</title>
  <style>{{ style }}</style>
</head>
<body>
{{ nav }}
<div class="container">
  {% if flash %}<div class="flash {{ flash_cls }}">{{ flash }}</div>{% endif %}

  <h2 style="color:#fff;margin-bottom:8px">Settings</h2>
  <p style="color:#555;font-size:0.9rem;margin-bottom:32px">
    These are saved to your <code style="color:#888">.env</code> file.
  </p>

  <form method="POST" action="/settings">

    <!-- YouTube credentials -->
    <div class="card" style="margin-bottom:20px">
      <div class="section-title">YouTube Login
        {% if yt_ok %}<span class="badge badge-ok">✓ configured</span>{% else %}<span class="badge badge-err">missing</span>{% endif %}
      </div>
      <div class="field">
        <label>YouTube / Google Email</label>
        <input type="email" name="YOUTUBE_EMAIL" value="{{ vals.YOUTUBE_EMAIL or '' }}" placeholder="you@gmail.com">
      </div>
      <div class="field" style="margin-bottom:0">
        <label>YouTube / Google Password</label>
        <input type="password" name="YOUTUBE_PASSWORD" value="{{ vals.YOUTUBE_PASSWORD or '' }}" placeholder="••••••••">
      </div>
    </div>

    <!-- Channel watermark -->
    <div class="card" style="margin-bottom:20px">
      <div class="section-title">Branding</div>
      <div class="field" style="margin-bottom:0">
        <label>Watermark Text</label>
        <input type="text" name="WATERMARK_TEXT" value="{{ vals.WATERMARK_TEXT or '@CatCentral' }}" placeholder="@CatCentral">
      </div>
    </div>

    <!-- Optional API keys -->
    <div class="card" style="margin-bottom:20px">
      <div class="section-title">Optional — YouTube Data API
        <span style="color:#444;font-weight:400;text-transform:none;letter-spacing:0;font-size:0.78rem">(not required — leave blank to use browser upload)</span>
      </div>
      <div class="field">
        <label>Google Client ID</label>
        <input type="text" name="GOOGLE_CLIENT_ID" value="{{ vals.GOOGLE_CLIENT_ID or '' }}" placeholder="xxxx.apps.googleusercontent.com">
      </div>
      <div class="field" style="margin-bottom:0">
        <label>Google Client Secret</label>
        <input type="password" name="GOOGLE_CLIENT_SECRET" value="{{ vals.GOOGLE_CLIENT_SECRET or '' }}" placeholder="••••••••">
      </div>
    </div>

    <!-- Optional Gemini -->
    <div class="card" style="margin-bottom:20px">
      <div class="section-title">Optional — Gemini AI
        <span style="color:#444;font-weight:400;text-transform:none;letter-spacing:0;font-size:0.78rem">(improves clip selection — leave blank to use audio detection)</span>
      </div>
      <div class="field" style="margin-bottom:0">
        <label>Gemini API Key</label>
        <input type="password" name="GEMINI_API_KEY" value="{{ vals.GEMINI_API_KEY or '' }}" placeholder="••••••••">
      </div>
    </div>

    <div style="display:flex;gap:12px;align-items:center">
      <button type="submit" class="btn">Save Settings</button>
      <a href="/"><button type="button" class="btn-ghost">Cancel</button></a>
    </div>

  </form>
</div>
</body>
</html>"""

# ── Setup page (first-time, no credentials) ────────────────────────────────────

SETUP = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Setup — CatCentral</title>
  <style>{{ style }}</style>
</head>
<body>
<div style="min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px">
  <div style="width:100%;max-width:460px">
    <div style="text-align:center;margin-bottom:32px">
      <div style="font-size:3rem;margin-bottom:8px">🐱</div>
      <h1 style="font-size:1.8rem;color:#fff;margin-bottom:6px">CatCentral Setup</h1>
      <p style="color:#555;font-size:0.9rem">Enter your YouTube credentials to get started</p>
    </div>

    {% if flash %}<div class="flash {{ flash_cls }}">{{ flash }}</div>{% endif %}

    <div class="card">
      <form method="POST" action="/setup">
        <div class="field">
          <label>YouTube / Google Email</label>
          <input type="email" name="YOUTUBE_EMAIL" placeholder="you@gmail.com" required autofocus>
        </div>
        <div class="field">
          <label>YouTube / Google Password</label>
          <input type="password" name="YOUTUBE_PASSWORD" placeholder="••••••••" required>
        </div>
        <div class="field" style="margin-bottom:24px">
          <label>Watermark Text</label>
          <input type="text" name="WATERMARK_TEXT" value="@CatCentral" placeholder="@CatCentral">
        </div>
        <button type="submit" class="btn" style="width:100%;padding:14px">Get Started</button>
      </form>
    </div>
    <p style="text-align:center;color:#444;font-size:0.8rem;margin-top:20px">
      You can add optional API keys later in Settings
    </p>
  </div>
</div>
</body>
</html>"""


def _render(template, **kwargs):
    v = _env_vals()
    yt_ok = bool(v.get("YOUTUBE_EMAIL") and v.get("YOUTUBE_PASSWORD"))
    return render_template_string(
        template,
        style=_BASE_STYLE,
        nav=_nav(kwargs.pop("active", "")),
        vals=v,
        yt_ok=yt_ok,
        flash=kwargs.pop("flash", ""),
        flash_cls=kwargs.pop("flash_cls", ""),
        **kwargs,
    )


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if not _is_configured():
        return redirect(url_for("setup"))
    return _render(HOME, active="home")


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if request.method == "POST":
        updates = {
            "YOUTUBE_EMAIL":    request.form.get("YOUTUBE_EMAIL", "").strip(),
            "YOUTUBE_PASSWORD": request.form.get("YOUTUBE_PASSWORD", "").strip(),
            "WATERMARK_TEXT":   request.form.get("WATERMARK_TEXT", "@CatCentral").strip(),
        }
        if not updates["YOUTUBE_EMAIL"] or not updates["YOUTUBE_PASSWORD"]:
            return _render(SETUP, flash="Email and password are required.", flash_cls="")
        _save_env(updates)
        return redirect(url_for("index"))
    if _is_configured():
        return redirect(url_for("index"))
    return _render(SETUP)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        fields = [
            "YOUTUBE_EMAIL", "YOUTUBE_PASSWORD", "WATERMARK_TEXT",
            "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GEMINI_API_KEY",
        ]
        updates = {f: request.form.get(f, "").strip() for f in fields}
        # Only save non-empty values (don't overwrite with blank)
        updates = {k: v for k, v in updates.items() if v}
        _save_env(updates)
        return _render(SETTINGS, active="settings",
                       flash="Settings saved.", flash_cls="ok")
    return _render(SETTINGS, active="settings")


# ── Pipeline runner ────────────────────────────────────────────────────────────

class _QueueHandler(logging.Handler):
    def emit(self, record):
        try:
            _log_queue.put(self.format(record))
        except Exception:
            pass


def _run_pipeline():
    global _running
    try:
        # Reload env so fresh credentials are picked up
        from dotenv import load_dotenv
        load_dotenv(str(ENV_PATH), override=True)

        from config import Config, setup_logging
        from src.scheduler import Pipeline

        config = Config()
        setup_logging(config)

        handler = _QueueHandler()
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S"))
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            Pipeline(config, dry_run=False).run()
        finally:
            root.removeHandler(handler)
    except Exception as e:
        _log_queue.put(f"[ERROR] {e}")
    finally:
        _log_queue.put("__DONE__")
        _running = False


@app.route("/run", methods=["POST"])
def run():
    global _running
    if not _is_configured():
        return {"ok": False, "error": "Not configured — go to Settings first."}
    if not _lock.acquire(blocking=False):
        return {"ok": False, "error": "Pipeline is already running."}
    _running = True
    while not _log_queue.empty():
        try:
            _log_queue.get_nowait()
        except queue.Empty:
            break
    threading.Thread(target=_run_pipeline, daemon=True).start()
    return {"ok": True}


@app.route("/stream")
def stream():
    def generate():
        while True:
            try:
                msg = _log_queue.get(timeout=30)
                yield f"data: {msg}\n\n"
                if msg == "__DONE__":
                    _lock.release()
                    break
            except queue.Empty:
                yield "data: (waiting…)\n\n"
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    import socket
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except Exception:
        local_ip = "127.0.0.1"

    print("\n  ╔══════════════════════════════════════════════╗")
    print("  ║   🐱  CatCentral — Web Interface            ║")
    print("  ╚══════════════════════════════════════════════╝\n")
    print(f"  Local:   http://127.0.0.1:5000")
    print(f"  Network: http://{local_ip}:5000  ← share this\n")

    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
