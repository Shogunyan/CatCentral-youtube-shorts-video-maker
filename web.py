"""
web.py — CatCentral local web interface.

Run:  python3 web.py
Then open http://YOUR-LOCAL-IP:5000 on any device on the same WiFi.
"""
import logging
import queue
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, Response, render_template_string

app = Flask(__name__)

# One pipeline at a time
_lock = threading.Lock()
_running = False
_log_queue: queue.Queue = queue.Queue()

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CatCentral</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #0d0d0d;
      color: #e0e0e0;
      font-family: 'Segoe UI', system-ui, sans-serif;
      display: flex;
      flex-direction: column;
      align-items: center;
      min-height: 100vh;
      padding: 48px 16px;
    }
    h1 {
      font-size: 2rem;
      letter-spacing: 2px;
      color: #fff;
      margin-bottom: 6px;
    }
    .sub {
      color: #888;
      font-size: 0.9rem;
      margin-bottom: 40px;
    }
    #run-btn {
      background: #ff4444;
      color: #fff;
      border: none;
      border-radius: 12px;
      padding: 18px 56px;
      font-size: 1.3rem;
      font-weight: 700;
      letter-spacing: 1px;
      cursor: pointer;
      transition: background 0.2s, transform 0.1s;
      user-select: none;
    }
    #run-btn:hover { background: #ff2222; transform: scale(1.03); }
    #run-btn:active { transform: scale(0.97); }
    #run-btn:disabled { background: #555; cursor: not-allowed; transform: none; }
    .hint {
      color: #555;
      font-size: 0.8rem;
      margin-top: 10px;
    }
    #log-box {
      margin-top: 40px;
      width: 100%;
      max-width: 820px;
      background: #111;
      border: 1px solid #222;
      border-radius: 10px;
      padding: 20px;
      font-family: 'Courier New', monospace;
      font-size: 0.82rem;
      line-height: 1.6;
      min-height: 120px;
      max-height: 60vh;
      overflow-y: auto;
      white-space: pre-wrap;
      word-break: break-all;
    }
    .log-line { color: #39ff14; }
    .log-err  { color: #ff4444; }
    .log-warn { color: #ffaa00; }
    .log-info { color: #39ff14; }
    .log-done { color: #00cfff; font-weight: bold; }
    #status {
      margin-top: 14px;
      font-size: 0.9rem;
      color: #888;
      height: 20px;
    }
  </style>
</head>
<body>
  <h1>🐱 CatCentral</h1>
  <p class="sub">Upload a ranked cat Short to YouTube in one click</p>

  <button id="run-btn" onclick="runPipeline()">▶ RUN PIPELINE</button>
  <p class="hint">or press <kbd>R</kbd></p>
  <p id="status"></p>

  <div id="log-box"></div>

  <script>
    let running = false;
    let es = null;

    document.addEventListener('keydown', e => {
      if (e.key === 'r' || e.key === 'R') runPipeline();
    });

    function setRunning(v) {
      running = v;
      document.getElementById('run-btn').disabled = v;
      document.getElementById('status').textContent = v ? 'Pipeline running…' : '';
    }

    function appendLog(text, cls) {
      const box = document.getElementById('log-box');
      const line = document.createElement('div');
      line.className = cls || 'log-line';
      line.textContent = text;
      box.appendChild(line);
      box.scrollTop = box.scrollHeight;
    }

    function classFor(text) {
      const t = text.toLowerCase();
      if (t.includes('error') || t.includes('failed') || t.includes('✗')) return 'log-err';
      if (t.includes('warn')) return 'log-warn';
      if (t.includes('✓') || t.includes('done') || t.includes('complete') || t.includes('published')) return 'log-done';
      return 'log-info';
    }

    function runPipeline() {
      if (running) return;
      setRunning(true);
      document.getElementById('log-box').innerHTML = '';

      if (es) { es.close(); es = null; }

      fetch('/run', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
          if (!data.ok) {
            appendLog(data.error || 'Could not start pipeline', 'log-err');
            setRunning(false);
            return;
          }
          es = new EventSource('/stream');
          es.onmessage = e => {
            if (e.data === '__DONE__') {
              es.close(); es = null;
              setRunning(false);
              document.getElementById('status').textContent = 'Finished!';
            } else {
              appendLog(e.data, classFor(e.data));
            }
          };
          es.onerror = () => {
            appendLog('Connection lost.', 'log-err');
            es.close(); es = null;
            setRunning(false);
          };
        })
        .catch(err => {
          appendLog('Request failed: ' + err, 'log-err');
          setRunning(false);
        });
    }
  </script>
</body>
</html>
"""


class _QueueHandler(logging.Handler):
    """Forwards log records to the SSE queue."""
    def emit(self, record):
        try:
            msg = self.format(record)
            _log_queue.put(msg)
        except Exception:
            pass


def _run_pipeline():
    global _running
    try:
        from config import Config, setup_logging
        from src.scheduler import Pipeline

        config = Config()
        setup_logging(config)

        # Attach our queue handler so pipeline logs flow to the browser
        handler = _QueueHandler()
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S"))
        root = logging.getLogger()
        root.addHandler(handler)

        try:
            pipeline = Pipeline(config, dry_run=False)
            pipeline.run()
        finally:
            root.removeHandler(handler)
    except Exception as e:
        _log_queue.put(f"[ERROR] {e}")
    finally:
        _log_queue.put("__DONE__")
        _running = False


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/run", methods=["POST"])
def run():
    global _running
    if not _lock.acquire(blocking=False):
        return {"ok": False, "error": "Pipeline is already running."}
    _running = True
    # Drain any leftover messages from a previous run
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
