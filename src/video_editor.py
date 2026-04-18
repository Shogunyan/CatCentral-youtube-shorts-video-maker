"""
video_editor.py — Builds a ranking-style YouTube Shorts video from cat clips.

Format (matching viral cat ranking channels):
  • NO intro card — jumps straight into clip #N
  • Bold title at the very top of every frame
  • Left-side numbered list showing all ranks simultaneously:
      - Current clip highlighted in gold (large number)
      - Already-shown clips dimmed
      - Upcoming clips show "?" (suspense)
  • Clips play full-screen behind the overlay
  • Moving @CatCentral watermark in corners
"""
import json as _json
import logging
import os
import re

import shutil
import subprocess
import tempfile

from pathlib import Path

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
TARGET_W = 1080
TARGET_H = 1920
FPS = 30
VIDEO_CODEC = "libx264"
AUDIO_CODEC = "aac"
AUDIO_BITRATE = "128k"
VIDEO_CRF = "18"


def _ffmpeg(*args, check=True) -> subprocess.CompletedProcess:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    logger.debug("ffmpeg: " + " ".join(str(a) for a in cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr}")
    return result


def _escape_drawtext(text: str) -> str:
    """Escape special chars for ffmpeg drawtext filter."""
    for ch in ("\\", ":", "'", "[", "]", "%"):
        text = text.replace(ch, "\\" + ch)
    return text


def _clean_title(title: str) -> str:
    """Strip hashtags and extra whitespace for clean on-screen display."""
    title = re.sub(r"\s*#\w+", "", title).strip()
    title = re.sub(r"\s{2,}", " ", title)
    return title.upper()


def _find_font(bubbly: bool = False) -> str:
    """Return a font path ffmpeg can use for drawtext."""
    if bubbly:
        candidates = [
            str(Path(__file__).parent.parent / "assets" / "fonts" / "Fredoka.ttf"),
            "C:/Windows/Fonts/comicbd.ttf",
        ]
        for p in candidates:
            if Path(p).exists():
                return p
    standard = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    for p in standard:
        if Path(p).exists():
            return p
    return ""


FONT_BUBBLY = _find_font(bubbly=True)
FONT_PLAIN  = _find_font(bubbly=False)
_FONT_B = f":fontfile={FONT_BUBBLY}" if FONT_BUBBLY else ""
_FONT_P = f":fontfile={FONT_PLAIN}"  if FONT_PLAIN  else ""


def _probe_duration(path: Path) -> float:
    """Return the duration of a video file in seconds, or 0.0 on failure."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


# ── Full-short mode functions ─────────────────────────────────────────────────

def _gemini_with_retry(fn, retries: int = 4, base_delay: float = 5.0):
    """Call fn(), retrying up to `retries` times on 429 rate-limit errors."""
    import time as _time
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as e:
            msg = str(e).lower()
            is_rate_limit = "429" in msg or "resource_exhausted" in msg or "quota" in msg
            if is_rate_limit and attempt < retries:
                wait = base_delay * (2 ** attempt)
                logger.info(f"  Gemini rate-limited — retrying in {wait:.0f}s (attempt {attempt+1}/{retries})")
                _time.sleep(wait)
            else:
                raise


def _gemini_generate_ve(api_key: str, model: str, parts: list) -> str:
    """Thin Gemini wrapper with retry on rate-limit errors."""
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        built = []
        for p in parts:
            if isinstance(p, str):
                built.append(types.Part.from_text(text=p))
            elif isinstance(p, dict) and "data" in p:
                built.append(types.Part.from_bytes(data=p["data"], mime_type=p.get("mime_type", "image/jpeg")))
            else:
                built.append(p)
        return _gemini_with_retry(
            lambda: client.models.generate_content(model=model, contents=built).text
        )
    except ImportError:
        import google.generativeai as genai  # type: ignore[no-redef]
        genai.configure(api_key=api_key)
        return _gemini_with_retry(
            lambda: genai.GenerativeModel(model).generate_content(parts).text
        )


def _extract_frame_ve(path: Path, timestamp: float) -> bytes | None:
    """Extract a single JPEG frame from a local video file."""
    fd, tmp_str = tempfile.mkstemp(suffix=".jpg")
    tmp = Path(tmp_str)
    try:
        os.close(fd)
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", f"{timestamp:.3f}", "-i", str(path),
             "-vframes", "1", "-q:v", "3", str(tmp)],
            check=True, capture_output=True, timeout=15,
        )
        if tmp.exists() and tmp.stat().st_size > 0:
            return tmp.read_bytes()
    except Exception:
        pass
    finally:
        tmp.unlink(missing_ok=True)
    return None


_OVERLAY_PROMPT = (
    "Watch this entire cat ranking YouTube Short.\n"
    "Identify EVERY text overlay, watermark, channel handle/logo, rank number or label, "
    "and any UI element burned in by the original creator — including elements that only "
    "appear briefly or fade in/out during the video.\n"
    "Do NOT include the actual cat footage or video background.\n\n"
    "For each element give its bounding box as a percentage of frame size (0–100).\n"
    "If an element moves or appears at multiple positions, return one box per position.\n"
    "Be generous — make each box ~10% larger than the visible element to ensure full coverage.\n\n"
    "Reply ONLY with valid JSON (no markdown):\n"
    '{"regions": [{"x": <left%>, "y": <top%>, "w": <width%>, "h": <height%>, "label": "<what it is>"}]}'
)


def _parse_overlay_regions(raw: str) -> list[tuple[int, int, int, int]]:
    """Parse Gemini's JSON response into pixel (x, y, w, h) tuples."""
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```\s*$", "", raw, flags=re.MULTILINE)
    brace = raw.find("{")
    if brace == -1:
        return []
    data = _json.loads(raw[brace:])
    regions: list[tuple[int, int, int, int]] = []
    pad = 12
    for r in data.get("regions", []):
        x = max(0,        int(r["x"] / 100 * 1080) - pad)
        y = max(0,        int(r["y"] / 100 * 1920) - pad)
        w = min(1080 - x, int(r["w"] / 100 * 1080) + pad * 2)
        h = min(1920 - y, int(r["h"] / 100 * 1920) + pad * 2)
        if w > 8 and h > 8:
            label = r.get("label", "?")
            logger.debug(f"    overlay: {label}  ({x},{y},{w},{h})")
            regions.append((x, y, w, h))
    return regions


def _gemini_detect_overlays(path: Path, api_key: str) -> list[tuple[int, int, int, int]]:
    """
    Upload the full video to Gemini Files API so it can WATCH the entire Short
    and identify every text overlay, watermark, and channel branding element —
    including ones that only appear briefly or animate in/out.

    Falls back to frame-sampling if the Files API upload fails, and to [] if
    Gemini is unavailable entirely. Never raises — safe to call unconditionally.
    """
    # ── Attempt 1: full-video upload via Files API ────────────────────────────
    try:
        import time as _time
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        logger.info("  Uploading Short to Gemini Files API for full-video analysis…")
        video_file = client.files.upload(
            path=str(path),
            config=types.UploadFileConfig(mime_type="video/mp4"),
        )
        # Wait for processing (usually a few seconds for a Short)
        for _ in range(30):
            if video_file.state.name != "PROCESSING":
                break
            _time.sleep(1)
            video_file = client.files.get(name=video_file.name)

        if video_file.state.name == "FAILED":
            raise RuntimeError("Gemini file processing failed")

        response = _gemini_with_retry(
            lambda: client.models.generate_content(
                model="gemini-2.0-flash",
                contents=[video_file, _OVERLAY_PROMPT],
            )
        )
        try:
            client.files.delete(name=video_file.name)
        except Exception:
            pass

        regions = _parse_overlay_regions(response.text.strip())
        logger.info(f"  Gemini (full video) detected {len(regions)} overlay region(s)")
        return regions

    except ImportError:
        pass  # google-genai not available — fall through to frame sampling
    except Exception as e:
        logger.debug(f"Gemini Files API failed ({e}), falling back to frame sampling")

    # ── Attempt 2: frame-sampling fallback (legacy SDK or Files API unavailable) ─
    try:
        duration = _probe_duration(path)
        if not duration or duration < 2:
            return []

        n = min(8, max(3, int(duration / 4)))
        margin = 0.5
        timestamps = [margin + (duration - 2 * margin) * i / max(1, n - 1) for i in range(n)]

        frame_data: list[tuple[float, bytes]] = []
        for ts in timestamps:
            fb = _extract_frame_ve(path, ts)
            if fb:
                frame_data.append((ts, fb))

        if len(frame_data) < 2:
            return []

        parts: list = [
            "You are analyzing frames from a cat ranking YouTube Short (1080×1920 px).\n"
            + _OVERLAY_PROMPT
            + f"\n\nVideo is {duration:.0f}s. Frames at: "
            + ", ".join(f"{t:.1f}s" for t, _ in frame_data) + "\n",
        ]
        for ts, fb in frame_data:
            parts.append(f"\n[Frame at {ts:.1f}s]:")
            parts.append({"mime_type": "image/jpeg", "data": fb})

        raw = _gemini_generate_ve(api_key, "gemini-2.0-flash", parts).strip()
        regions = _parse_overlay_regions(raw)
        logger.info(f"  Gemini (frame sampling) detected {len(regions)} overlay region(s)")
        return regions

    except Exception as e:
        logger.debug(f"Gemini frame-sampling fallback failed: {e}")
        return []


def _blur_targeted_regions(
    src: Path, dst: Path, regions: list[tuple[int, int, int, int]]
) -> None:
    """
    Apply targeted gaussian blur to specific (x, y, w, h) pixel regions on a
    1080×1920 video. Used on top of the fixed-bar baseline blur so that
    any mid-frame watermarks Gemini detected are also removed.
    gblur is used instead of boxblur (boxblur chroma radius is capped at 17).
    """
    if not regions:
        shutil.copy2(src, dst)
        return

    n = len(regions)
    split_labels = "".join(f"[c{i}]" for i in range(n))
    fc = [f"[0:v]split={n + 1}[base]{split_labels}"]
    for i, (x, y, w, h) in enumerate(regions):
        fc.append(f"[c{i}]crop={w}:{h}:{x}:{y},gblur=sigma=20[b{i}]")
    prev = "base"
    for i, (x, y, w, h) in enumerate(regions):
        nxt = "out" if i == n - 1 else f"o{i}"
        fc.append(f"[{prev}][b{i}]overlay={x}:{y}[{nxt}]")
        prev = nxt

    _ffmpeg(
        "-i", str(src),
        "-filter_complex", ";".join(fc),
        "-map", "[out]", "-map", "0:a?",
        "-c:v", VIDEO_CODEC, "-crf", "18", "-preset", "fast",
        "-c:a", "copy",
        str(dst),
    )


def _strip_emoji(text: str) -> str:
    """Remove emoji that ffmpeg drawtext can't render (shows as empty boxes)."""
    return re.sub(
        r"[\U0001F300-\U0001FAFF"   # symbols, pictographs, transport, flags
        r"\U00002600-\U000027BF"    # misc symbols & dingbats
        r"\U0000FE00-\U0000FE0F"    # variation selectors
        r"\U0001F900-\U0001F9FF"    # supplemental symbols
        r"\u200d\uFE0F]+",          # ZWJ and variation selector-16
        "", text,
    ).strip()


def _blur_text_regions(src: Path, dst: Path) -> None:
    """
    Scale to 1080×1920 and blur the original creator's text regions:
      • Top 160px        — title bar / channel name header
      • Bottom 220px     — username, music info, like/comment/share buttons
      • Left 70px strip  — side rank panels (common in ranking Shorts)
    Uses gblur (gaussian) — boxblur's chroma radius is capped at 17 in ffmpeg.
    Audio is stream-copied (no re-encode).
    """
    _ffmpeg(
        "-i", str(src),
        "-filter_complex",
        (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920[scaled];"
            "[scaled]split=4[v1][v2][v3][v4];"
            # Top 160px — header/title bar
            "[v2]crop=1080:160:0:0,gblur=sigma=20[btop];"
            # Bottom 220px — username, music, action buttons
            "[v3]crop=1080:220:0:1700,gblur=sigma=20[bbot];"
            # Left 70px strip — side rank number panels
            "[v4]crop=70:1920:0:0,gblur=sigma=20[bleft];"
            "[v1][btop]overlay=0:0[o1];"
            "[o1][bbot]overlay=0:1700[o2];"
            "[o2][bleft]overlay=0:0[out]"
        ),
        "-map", "[out]", "-map", "0:a?",
        "-c:v", VIDEO_CODEC, "-crf", "18", "-preset", "fast",
        "-c:a", "copy",
        str(dst),
    )


def _add_branding(
    input_path: Path,
    output_path: Path,
    title: str,
    watermark_text: str,
) -> Path:
    """
    Single ffmpeg pass that adds:
      • Our title text centred in the top blurred bar
      • Moving @CatCentral watermark in corners
      • Like & Subscribe popup badge (t=3..6)
    """
    wm  = _escape_drawtext(watermark_text)
    # Strip emoji (ffmpeg drawtext renders them as empty boxes), then clean + escape
    ttl = _escape_drawtext(_clean_title(_strip_emoji(title)))
    # Hard-truncate so long titles don't overflow and shift off-screen
    if len(ttl) > 42:
        ttl = ttl[:40] + "..."
    pad = 55

    # Moving watermark — cycles through 4 corner positions every 12 s
    x_expr = (
        f"if(eq(mod(floor(t/12),4),0),{pad},"
        f"if(eq(mod(floor(t/12),4),1),w-tw-{pad},"
        f"if(eq(mod(floor(t/12),4),2),{pad},"
        f"w-tw-{pad})))"
    )
    y_expr = (
        f"if(eq(mod(floor(t/12),4),0),{pad+20},"
        f"if(eq(mod(floor(t/12),4),1),{pad+20},"
        f"if(eq(mod(floor(t/12),4),2),h-th-{pad},"
        f"h-th-{pad})))"
    )

    # Title in top bar — max(10,...) prevents negative x when title is wide
    title_filter = (
        f"drawtext=text='{ttl}'{_FONT_B}"
        ":fontsize=38:fontcolor=white"
        ":borderw=3:bordercolor=black@0.8"
        ":x=max(10\\,(w-tw)/2):y=58"
    )
    wm_filter = (
        f"drawtext=text='{wm}'{_FONT_P}"
        ":fontsize=34:fontcolor=white@0.75"
        ":borderw=2:bordercolor=black@0.6"
        f":x='{x_expr}':y='{y_expr}'"
    )
    # L&S popup — centred, y=1710 = 1920-210
    popup_box = (
        "drawbox=x=280:y=1710:w=520:h=88"
        ":color=#EE1111@0.88:t=fill"
        ":enable='between(t,3,6)'"
    )
    popup_text = (
        f"drawtext=text='LIKE \\& SUBSCRIBE'{_FONT_B}"
        ":fontsize=40:fontcolor=white"
        ":borderw=3:bordercolor=black@0.8"
        ":x=(w-tw)/2:y=1728"
        ":enable='between(t,3,6)'"
    )
    popup_hint = (
        f"drawtext=text='for more cat videos'{_FONT_P}"
        ":fontsize=24:fontcolor=white@0.8"
        ":borderw=2:bordercolor=black@0.6"
        ":x=(w-tw)/2:y=1768"
        ":enable='between(t,3,6)'"
    )

    vf = f"{title_filter},{wm_filter},{popup_box},{popup_text},{popup_hint}"

    _ffmpeg(
        "-i", str(input_path),
        "-vf", vf,
        "-c:v", VIDEO_CODEC, "-crf", VIDEO_CRF, "-preset", "fast",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    )
    return output_path


def create_full_short_ranking_video(
    source_path: Path,
    rank_segments: list[dict],
    title: str,
    output_path: Path,
    config,
    on_progress=None,
) -> None:
    """
    Build a branded ranking Short:
      1. Scale to 1080×1920, blur top + bottom text bars
      2. Overlay our title at top, moving watermark, L&S popup
    No Gemini segmentation — the original Short is kept intact.
    """
    watermark_text = getattr(config, "watermark_text", "@CatCentral")
    api_key = os.getenv("GEMINI_API_KEY", "")

    # Step 1: fixed baseline blur (scale + top/bottom bars + left side strip)
    if on_progress:
        on_progress("Blurring original text overlays…")
    blurred = source_path.with_name(f"_blurred_{source_path.stem}.mp4")
    _blur_text_regions(source_path, blurred)

    # Step 2: Gemini pinpoints any remaining mid-frame watermarks/overlays
    if api_key:
        if on_progress:
            on_progress("Gemini scanning for watermark positions…")
        gemini_regions = _gemini_detect_overlays(source_path, api_key)
        if gemini_regions:
            if on_progress:
                on_progress(f"Blurring {len(gemini_regions)} detected overlay(s)…")
            targeted = source_path.with_name(f"_targeted_{source_path.stem}.mp4")
            _blur_targeted_regions(blurred, targeted, gemini_regions)
            blurred.unlink(missing_ok=True)
            blurred = targeted

    # Step 3: add our title, watermark, and L&S popup
    if on_progress:
        on_progress("Adding CatCentral title and watermark…")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = _add_branding(blurred, output_path, title, watermark_text)
    blurred.unlink(missing_ok=True)

    if on_progress:
        on_progress("Done — video ready.")

    if not result or not result.exists():
        raise RuntimeError("Branding step failed for full-short mode")


# ── CLI helper ────────────────────────────────────────────────────────────────

def check_ffmpeg():
    """Raise RuntimeError if ffmpeg or ffprobe is not installed."""
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError(
                f"{tool} is not installed or not on PATH.\n"
                "Install: sudo apt install ffmpeg"
            )
