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


def _merge_regions(
    regions: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    """Merge overlapping or within-20px-proximity bounding boxes."""
    if not regions:
        return []
    boxes = [(x, y, x + w, y + h) for x, y, w, h in regions]
    changed = True
    while changed:
        changed = False
        merged: list[tuple[int, int, int, int]] = []
        used = set()
        for i, a in enumerate(boxes):
            if i in used:
                continue
            x1, y1, x2, y2 = a
            for j, b in enumerate(boxes):
                if j <= i or j in used:
                    continue
                pad = 20
                if a[0] - pad < b[2] and a[2] + pad > b[0] and \
                   a[1] - pad < b[3] and a[3] + pad > b[1]:
                    x1 = min(x1, b[0]); y1 = min(y1, b[1])
                    x2 = max(x2, b[2]); y2 = max(y2, b[3])
                    used.add(j)
                    changed = True
            used.add(i)
            merged.append((x1, y1, x2, y2))
        boxes = merged
    return [(x1, y1, x2 - x1, y2 - y1) for x1, y1, x2, y2 in boxes]


def _easyocr_detect_overlays(path: Path) -> list[tuple[int, int, int, int]]:
    """
    Extract frames from the video, run EasyOCR to find all text regions,
    and return pixel (x, y, w, h) bounding boxes for targeted blurring.
    Completely local — no API calls, no rate limits.
    """
    try:
        import easyocr
        import numpy as np
        from PIL import Image
        import io as _io
    except ImportError as e:
        logger.warning(f"EasyOCR deps missing ({e}) — skipping watermark scan. Run: pip install easyocr pillow numpy")
        return []

    try:
        duration = _probe_duration(path)
        if not duration or duration < 2:
            return []

        # Sample frames spread across the video (skip first/last 1s)
        n = min(8, max(3, int(duration / 8)))
        margin = 1.0
        timestamps = [
            margin + (duration - 2 * margin) * i / max(1, n - 1)
            for i in range(n)
        ]

        logger.info(f"  EasyOCR: scanning {n} frames for watermarks…")
        reader = easyocr.Reader(["en"], gpu=False, verbose=False)

        raw_regions: list[tuple[int, int, int, int]] = []
        for ts in timestamps:
            frame_bytes = _extract_frame_ve(path, ts)
            if not frame_bytes:
                continue
            img = np.array(Image.open(_io.BytesIO(frame_bytes)).convert("RGB"))
            for (bbox, text, conf) in reader.readtext(img):
                if conf < 0.3 or not text.strip():
                    continue
                xs = [int(p[0]) for p in bbox]
                ys = [int(p[1]) for p in bbox]
                pad = 14
                x = max(0,       min(xs) - pad)
                y = max(0,       min(ys) - pad)
                w = min(1080 - x, max(xs) - min(xs) + pad * 2)
                h = min(1920 - y, max(ys) - min(ys) + pad * 2)
                if w > 10 and h > 10:
                    raw_regions.append((x, y, w, h))

        merged = _merge_regions(raw_regions)

        # Drop regions already fully covered by the baseline blur zones:
        #   top 160px | bottom from y=1700 | left 70px
        final = [
            (x, y, w, h) for x, y, w, h in merged
            if not (y + h <= 160 or y >= 1700 or x + w <= 70)
        ]

        logger.info(f"  EasyOCR detected {len(final)} mid-frame overlay region(s)")
        return final

    except Exception as e:
        logger.warning(f"EasyOCR watermark scan failed: {e}")
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

    # Step 1: fixed baseline blur (scale + top/bottom bars + left side strip)
    if on_progress:
        on_progress("Blurring original text overlays…")
    blurred = source_path.with_name(f"_blurred_{source_path.stem}.mp4")
    _blur_text_regions(source_path, blurred)

    # Step 2: EasyOCR pinpoints any remaining mid-frame watermarks/overlays
    if on_progress:
        on_progress("Scanning for mid-frame watermarks…")
    ocr_regions = _easyocr_detect_overlays(source_path)
    if ocr_regions:
        if on_progress:
            on_progress(f"Blurring {len(ocr_regions)} detected overlay(s)…")
        targeted = source_path.with_name(f"_targeted_{source_path.stem}.mp4")
        _blur_targeted_regions(blurred, targeted, ocr_regions)
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
