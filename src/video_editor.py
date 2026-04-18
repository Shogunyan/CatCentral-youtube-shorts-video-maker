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


# ── Sound effects ─────────────────────────────────────────────────────────────

_DING_PATH = Path(__file__).parent.parent / "assets" / "sfx" / "ding.mp3"

_DING_VERSION = 1   # bump to force regeneration when synthesis changes


def _get_ding() -> Path | None:
    """
    Return path to the rank-reveal ding sound, generating it if needed.

    Generates a bright bell-like ding (880 Hz + 1760 Hz octave harmonic)
    with a quick attack and smooth decay — the same style used by viral
    Reddit-story and ranking YouTube channels.
    """
    ver_file = _DING_PATH.with_suffix(".ver")
    needs_regen = (
        not _DING_PATH.exists()
        or not ver_file.exists()
        or ver_file.read_text().strip() != str(_DING_VERSION)
    )
    if not needs_regen:
        return _DING_PATH
    try:
        _DING_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Bell ding: mix 880 Hz (fundamental) + 1760 Hz (octave) sine waves,
        # fast attack (10 ms), smooth decay over 0.6 s → bright notification ding.
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=880:duration=0.8",
                "-f", "lavfi", "-i", "sine=frequency=1760:duration=0.8",
                "-filter_complex",
                (
                    "[0][1]amix=inputs=2:weights=1 0.4,"
                    "afade=t=in:d=0.01,"
                    "afade=t=out:st=0.25:d=0.55,"
                    "volume=3.0"
                ),
                "-ar", "44100", "-ac", "2",
                str(_DING_PATH),
            ],
            check=True,
            capture_output=True,
        )
        ver_file.write_text(str(_DING_VERSION))
        logger.info(f"Generated ding SFX (v{_DING_VERSION}) → {_DING_PATH}")
        return _DING_PATH
    except Exception as e:
        logger.warning(f"Could not generate ding sound: {e}")
        return None


def _has_audio(video_path: Path) -> bool:
    """Return True if the video file has at least one audio stream."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            str(video_path),
        ],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() == "audio"


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


# ── Ranking overlay ───────────────────────────────────────────────────────────


# Single-word swaps applied to screen labels copied from the source video.
# Keeps the label recognisable but different enough to not be identical.
_LABEL_SWAPS: dict[str, str] = {
    "worst":    "last",    "best":     "top",     "funniest": "wildest",
    "funny":    "wild",    "bad":      "mid",     "good":     "nice",
    "top":      "peak",    "goat":     "king",    "first":    "start",
    "last":     "end",     "rank":     "pick",    "number":   "slot",
    "worst":    "bottom",  "winner":   "champ",   "loser":    "last",
    "greatest": "peak",    "ultimate": "supreme", "epic":     "wild",
    "terrible": "rough",   "amazing":  "insane",  "awful":    "rough",
    "hilarious":"chaotic", "crazy":    "wild",    "insane":   "unreal",
}


def _alter_screen_label(text: str) -> str:
    """
    Swap one word in a verbatim screen label so our sidebar differs slightly
    from the original video's text. Only swaps the first matching word.
    """
    words = text.split()
    for i, w in enumerate(words):
        key = w.lower().strip("#.!?,")
        if key in _LABEL_SWAPS:
            words[i] = _LABEL_SWAPS[key].upper()
            break
    return " ".join(words)


# Fun clip labels used when the source title is generic (e.g. even-sliced clips).
# Indexed by rank position (0 = rank 1 / best clip, 4 = rank 5 / worst).
_RANK_LABELS = [
    # rank 1 — best
    ["THE GOAT", "UNREAL", "FINAL BOSS", "CERTIFIED", "PEAK CAT"],
    # rank 2
    ["SO CLOSE", "ALMOST", "RUNNER UP", "NOT BAD", "TOP TIER"],
    # rank 3
    ["SOLID 3", "MID CHAOS", "DECENT", "PRETTY WILD", "NO NOTES"],
    # rank 4
    ["BARELY", "LUCKY", "JUST MADE IT", "MAIN EVENT", "PLOT TWIST"],
    # rank 5 — worst (shown first)
    ["LAST PLACE", "STARTING OFF", "WARM UP", "ENTRY LEVEL", "SEND HELP"],
]


def _make_short_label(title: str, rank: int = 0, n_clips: int = 5) -> str:
    """
    Turn a clip title into a 2-word ALL-CAPS sidebar label.
    Falls back to a fun rank-appropriate label when the title is generic.
    rank=1 means best clip, rank=n_clips means worst/first shown.
    """
    # Detect generic "scene X" titles from even-slicing and blank titles
    clean = title.strip().lower()
    is_generic = (
        not clean
        or re.match(r'^scene\s*\d+$', clean)
        or re.match(r'^clip\s*\d+$', clean)
        or clean in {"untitled", "cat", "cats", "video", "clip"}
    )
    if is_generic:
        # rank 1 = best = last clip shown → index 0 in _RANK_LABELS
        # rank n = worst = first clip shown → index 4
        slot = max(0, min(4, n_clips - rank))
        pool = _RANK_LABELS[slot]
        # Deterministic pick per title string so the same clip always gets same label
        import hashlib
        idx = int(hashlib.md5(title.encode()).hexdigest(), 16) % len(pool)
        return pool[idx]

    # Strip hashtags, URLs, leading punctuation
    label = re.sub(r"#\w+", "", title).strip()
    label = re.sub(r"https?://\S+", "", label).strip()
    label = re.sub(r"^\W+", "", label).strip()

    # If this is a copied screen label (short, no sentence structure), alter
    # one word so our sidebar reads differently from the original video's text.
    words_raw = label.split()
    if len(words_raw) <= 4 and not any(w.lower() in {
        "the","a","an","of","in","on","at","to","and","or","but","is","it",
        "slides","falls","jumps","runs","yells","sits","stares","climbs",
    } for w in words_raw):
        return _alter_screen_label(label.upper())[:14] or "CAT CLIP"

    # Long description title — skip filler words, surface content words
    FILLER = {
        "the","a","an","of","in","on","at","to","and","or","but","is","it",
        "this","that","my","your","his","her","cat","cats","kitten","funny",
        "video","clip","short","shorts","when","how","why","what","who",
    }
    words = [w for w in label.split() if w.lower() not in FILLER]
    if not words:
        words = label.split()
    chosen = " ".join(words[:2])
    return chosen[:14].upper() or "CAT CLIP"


def _build_ranking_overlay(
    all_labels: list[str],
    current_idx: int,
    n: int,
    title: str,
) -> str:
    """
    Build the ffmpeg drawtext/drawbox filter chain for the ranking overlay.

    Layout:
      [Black bar at top — contains the video title]
      [Black panel on left — contains numbered list of all clips]
         Clips already shown: dimmed white
         Current clip: gold + large
         Clips still coming: white @ low opacity + "?"
    """
    parts: list[str] = []

    # ── Title bar ─────────────────────────────────────────────────────────────
    title_text = _escape_drawtext(_clean_title(title))
    title_fontsize = 52
    if len(title_text) > 22:
        title_fontsize = 44
    if len(title_text) > 30:
        title_fontsize = 36

    parts.append("drawbox=x=0:y=0:w=iw:h=118:color=black@0.78:t=fill")
    parts.append(
        f"drawtext=text='{title_text}'{_FONT_B}"
        f":fontsize={title_fontsize}:fontcolor=white"
        ":borderw=4:bordercolor=black@0.95"
        ":x=(w-tw)/2:y=38"
    )

    # ── Left panel ────────────────────────────────────────────────────────────

    # Item positions — spread evenly between y=155 and y=1820
    y_start  = 165
    y_end    = 1820
    spacing  = (y_end - y_start) // n

    for i in range(n):
        rank = n - i           # n → 1
        y    = y_start + i * spacing
        is_current = (i == current_idx)
        is_past    = (i < current_idx)

        if is_current:
            num_color = "#FFD700"
            num_size  = 112          # was 84
            lbl_color = "#FFFFFF"
            lbl_size  = 56           # was 40
        elif is_past:
            num_color = "white@0.55"
            num_size  = 76           # was 54
            lbl_color = "white@0.55"
            lbl_size  = 40           # was 28
        else:
            num_color = "white@0.28"
            num_size  = 76           # was 54
            lbl_color = "white@0.28"
            lbl_size  = 40           # was 28

        # Rank number
        num_str = _escape_drawtext(f"{rank}.")
        parts.append(
            f"drawtext=text='{num_str}'{_FONT_B}"
            f":fontsize={num_size}:fontcolor={num_color}"
            f":borderw=5:bordercolor=black@0.95"   # was borderw=3
            f":x=16:y={y}"
        )

        # Label: revealed for current + past; "?" for future
        if is_current or is_past:
            raw_lbl = _make_short_label(all_labels[i] if i < len(all_labels) else "")
            lbl_str = _escape_drawtext(raw_lbl)
        else:
            lbl_str = "\\?"

        lbl_y = y + max(0, (num_size - lbl_size) // 2 + 4)
        parts.append(
            f"drawtext=text='{lbl_str}'{_FONT_B}"
            f":fontsize={lbl_size}:fontcolor={lbl_color}"
            f":borderw=4:bordercolor=black@0.9"    # was borderw=2
            f":x=108:y={lbl_y}"
        )

    return ",".join(parts)


# ── Process one clip ──────────────────────────────────────────────────────────

def _process_clip(
    input_path: Path,
    output_path: Path,
    rank: int,
    clip_duration: int,
    title: str,
    all_labels: list[str] | None = None,
    current_idx: int = 0,
    n: int = 5,
) -> Path:
    """Scale/crop clip to 1080×1920, trim, and burn in the ranking overlay."""
    # Normalise SAR first (some downloads carry non-square pixel ratios),
    # then scale so the video COVERS the full 1080×1920 canvas without black
    # bars (force_original_aspect_ratio=increase), then centre-crop to exact size.
    scale_crop = (
        f"setsar=1,"
        f"scale={TARGET_W}:{TARGET_H}"
        f":force_original_aspect_ratio=increase"
        f":flags=lanczos,"
        f"crop={TARGET_W}:{TARGET_H}"
    )
    overlay = _build_ranking_overlay(
        all_labels=all_labels or [""] * n,
        current_idx=current_idx,
        n=n,
        title=title,
    )
    vf = f"{scale_crop},fps={FPS},{overlay}"

    _ffmpeg(
        "-i", str(input_path),
        "-t", str(clip_duration),
        "-vf", vf,
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-c:v", VIDEO_CODEC,
        "-crf", VIDEO_CRF,
        "-preset", "fast",
        "-c:a", AUDIO_CODEC,
        "-b:a", AUDIO_BITRATE,
        "-ar", "44100",
        "-ac", "2",
        "-movflags", "+faststart",
        str(output_path),
    )
    return output_path


# ── Concatenate ───────────────────────────────────────────────────────────────

def _concat_clips(clip_paths: list[Path], output_path: Path) -> Path:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for p in clip_paths:
            f.write(f"file '{str(p).replace(chr(92), '/')}'\n")
        list_file = Path(f.name)
    _ffmpeg(
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(output_path),
    )
    list_file.unlink(missing_ok=True)
    return output_path


# ── Watermark + popups ────────────────────────────────────────────────────────

def _add_watermark(input_path: Path, output_path: Path, watermark_text: str) -> Path:
    """
    Burn a moving @CatCentral watermark AND a like/subscribe popup into
    the video in a single ffmpeg pass.

    Like & Subscribe badge:
      • Red pill-shaped box, centred near the bottom
      • Appears 3–6 seconds in (during the first clip)
      • Short enough to be non-annoying, long enough to register
    """
    wm  = _escape_drawtext(watermark_text)
    pad = 55

    # Moving watermark
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
    wm_filter = (
        f"drawtext=text='{wm}'{_FONT_P}"
        ":fontsize=34:fontcolor=white@0.75"
        ":borderw=2:bordercolor=black@0.6"
        f":x='{x_expr}':y='{y_expr}'"
    )

    # Like & subscribe popup badge — shows at t=3..6
    # Use fixed pixel coords (1080×1920 frame) — drawbox doesn't support iw/ih expressions in all ffmpeg builds
    # x=280 = (1080-520)/2, y=1710 = 1920-210
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

    vf = f"{wm_filter},{popup_box},{popup_text},{popup_hint}"

    _ffmpeg(
        "-i", str(input_path),
        "-vf", vf,
        "-c:v", VIDEO_CODEC, "-crf", VIDEO_CRF, "-preset", "fast",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    )
    return output_path


# ── Platform watermark blur ───────────────────────────────────────────────────

_PLATFORM_BLUR_REGIONS: dict[str, list[tuple]] = {
    "youtube":   [],
    "instagram": [],
    "tiktok": [
        ("iw-180", "ih-180", 180, 180),
        ("0",      "ih-100", 300, 100),
    ],
    # Clips sliced directly from a ranking/countdown video retain the original
    # creator's overlays (title bar + left-side rank panel). Always blur these
    # regions so they don't conflict with our own ranking overlay.
    "ranking_slice": [
        ("0", "0",   "iw", 118),  # title/header bar — full width
        ("0", "118", 215, 1800),  # left-side rank number panel
    ],
    "unknown": [
        ("0",        "0",      180, 100),
        ("iw-180",   "0",      180, 100),
        ("0",        "ih-100", 180, 100),
        ("iw-180",   "ih-100", 180, 100),
        ("iw/2-200", "ih-80",  400,  80),
    ],
}
_BLUR_STRENGTH = 18
_WATERMARK_STDDEV_THRESHOLD   = 22.0
_WATERMARK_TEMPORAL_THRESHOLD = 20.0


def _get_video_size(video_path: Path) -> tuple[int, int]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True,
    )
    try:
        parts = result.stdout.strip().split(",")
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        logger.debug(f"Could not parse video size for {video_path.name}; using defaults")
        return 1080, 1920   # portrait default (Shorts format)


def _px(expr: str, iw: int, ih: int) -> int:
    """
    Evaluate a simple pixel expression like 'iw-180' or 'iw/2-200'.
    Only digits, +, -, *, / and parentheses are allowed after variable substitution.
    """
    s = str(expr).replace("iw", str(iw)).replace("ih", str(ih))
    if not re.match(r"^[\d\s+\-*/()]+$", s):
        return 0
    try:
        return int(eval(s))  # noqa: S307 — values are from hardcoded _PLATFORM_BLUR_REGIONS
    except Exception:
        return 0


def _resolve_region(region: tuple, vw: int, vh: int) -> tuple[int, int, int, int]:
    cx_expr, cy_expr, bw, bh = region
    cx = max(0, min(_px(cx_expr, vw, vh), vw - bw))
    cy = max(0, min(_px(cy_expr, vw, vh), vh - bh))
    return cx, cy, bw, bh


def _get_region_pixels(video_path: Path, x: int, y: int, w: int, h: int, seek: float) -> bytes | None:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-ss", str(seek), "-i", str(video_path),
         "-vframes", "1", "-vf", f"crop={w}:{h}:{x}:{y}",
         "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
        capture_output=True,
    )
    if result.returncode != 0 or len(result.stdout) < w * h // 2:
        return None
    return result.stdout[: w * h]


def _region_has_watermark(video_path: Path, x: int, y: int, w: int, h: int) -> bool:
    px1 = _get_region_pixels(video_path, x, y, w, h, seek=1.0)
    if px1 is None:
        return True
    n   = len(px1)
    m1  = sum(px1) / n
    std = (sum((p - m1) ** 2 for p in px1) / n) ** 0.5
    if std < _WATERMARK_STDDEV_THRESHOLD:
        return False
    px2 = _get_region_pixels(video_path, x, y, w, h, seek=3.0)
    if px2 is not None and len(px2) == n:
        diff = sum(abs(a - b) for a, b in zip(px1, px2)) / n
        return diff < _WATERMARK_TEMPORAL_THRESHOLD
    return True


def _blur_source_watermarks(input_path: Path, output_path: Path, platform: str = "unknown") -> Path:
    candidate_regions = _PLATFORM_BLUR_REGIONS.get(platform, _PLATFORM_BLUR_REGIONS["unknown"])
    if not candidate_regions:
        shutil.copy2(input_path, output_path)
        return output_path
    try:
        vw, vh = _get_video_size(input_path)
    except Exception:
        vw, vh = 1920, 1080
    if platform == "ranking_slice":
        # Ranking video overlays are always present — skip detection and
        # unconditionally blur the title bar + left rank panel.
        regions_to_blur = list(candidate_regions)
    else:
        regions_to_blur = []
        for region in candidate_regions:
            x, y, bw, bh = _resolve_region(region, vw, vh)
            if _region_has_watermark(input_path, x, y, bw, bh):
                regions_to_blur.append(region)
    if not regions_to_blur:
        shutil.copy2(input_path, output_path)
        return output_path
    n = len(regions_to_blur)
    split_labels = "".join(f"[c{i}]" for i in range(n))
    fc_parts = [f"[0:v]split={n + 1}[base]{split_labels}"]
    for i, (cx, cy, bw, bh) in enumerate(regions_to_blur):
        fc_parts.append(f"[c{i}]crop={bw}:{bh}:{cx}:{cy},gblur=sigma={_BLUR_STRENGTH}[b{i}]")
    prev = "base"
    for i, (cx, cy, bw, bh) in enumerate(regions_to_blur):
        ox  = cx.replace("iw", "W")
        oy  = cy.replace("ih", "H")
        nxt = "out" if i == n - 1 else f"o{i}"
        fc_parts.append(f"[{prev}][b{i}]overlay={ox}:{oy}[{nxt}]")
        prev = nxt
    try:
        _ffmpeg(
            "-i", str(input_path),
            "-filter_complex", ";".join(fc_parts),
            "-map", "[out]", "-map", "0:a?",
            "-c:v", VIDEO_CODEC, "-crf", VIDEO_CRF, "-preset", "fast",
            "-c:a", "copy",
            str(output_path),
        )
    except Exception as e:
        logger.warning(f"Watermark blur failed ({platform}): {e}")
        shutil.copy2(input_path, output_path)
    return output_path


# ── Full-short mode functions ─────────────────────────────────────────────────

def _gemini_generate_ve(api_key: str, model: str, parts: list) -> str:
    """Thin Gemini wrapper (local to video_editor — avoids cross-module import)."""
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
        return client.models.generate_content(model=model, contents=built).text
    except ImportError:
        import google.generativeai as genai  # type: ignore[no-redef]
        genai.configure(api_key=api_key)
        return genai.GenerativeModel(model).generate_content(parts).text


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

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[video_file, _OVERLAY_PROMPT],
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
            "[o2][bleft]overlay=0:0"
        ),
        "-map", "0:a?",
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
