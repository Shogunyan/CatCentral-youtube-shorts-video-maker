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
import multiprocessing
import shutil
import subprocess
import tempfile
import uuid
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


# ── Remotion renderer ─────────────────────────────────────────────────────────

_REMOTION_DIR = Path(__file__).parent.parent / "remotion"


def _is_real_browser_binary(path: str) -> bool:
    """
    Return True only if the path is a real ELF executable, not a shell wrapper.

    Ubuntu ships /usr/bin/chromium-browser as a shell script that prints
    "requires the chromium snap to be installed" and exits 1.  Passing such
    a wrapper to Remotion causes "Failed to launch the browser process!" and
    an opaque ENOENT error.  We exclude it by checking the ELF magic bytes.
    """
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"\x7fELF"
    except OSError:
        return False


def _browser_launches(path: str) -> bool:
    """
    Run the browser with --version to confirm it can actually start.

    This catches the common case where a binary exists and is a real ELF file
    but can't launch because it's missing shared libraries (libnss3, libgbm1,
    etc.).  Those binaries exit non-zero with an error like:
      'error while loading shared libraries: libnss3.so: No such file or directory'
    which Remotion then surfaces as an opaque ENOENT render failure.
    """
    try:
        r = subprocess.run(
            [path, "--version"],
            capture_output=True, text=True, timeout=8,
        )
        ok = r.returncode == 0
        if not ok:
            logger.debug(f"Browser {path} failed --version (rc={r.returncode}): {(r.stderr or r.stdout)[:200]}")
        return ok
    except Exception as exc:
        logger.debug(f"Browser {path} --version threw: {exc}")
        return False


def _find_headless_browser() -> str | None:
    """
    Return the path of a headless browser that Remotion can use.

    Every candidate is validated with --version before being returned, so we
    never hand Remotion a binary that can't launch (e.g. missing shared libs).

    Priority:
      1. REMOTION_CHROME_EXECUTABLE env var  (explicit user override)
      2. Playwright's headless_shell  ← best: stripped binary, deps handled
      3. Playwright's full chromium   ← good: deps installed by playwright
      4. Remotion's own downloaded chrome-headless-shell
      5. System headless-shell binaries
      6. System full Chrome/Chromium (ELF only — skips snap wrapper scripts)
    """
    def _accept(path: str, label: str) -> str | None:
        """Return path if it's a real binary AND can actually launch."""
        if not _is_real_browser_binary(path):
            logger.debug(f"Skipping {path} — not an ELF binary (likely a snap wrapper)")
            return None
        if not _browser_launches(path):
            logger.warning(
                f"Skipping {path} — binary exists but cannot launch.\n"
                f"  This usually means missing system libraries.  Fix:\n"
                f"    npx playwright install-deps chromium\n"
                f"  OR: sudo apt-get install -y libnss3 libgbm1 libasound2 "
                f"libatk1.0-0 libatk-bridge2.0-0 libxdamage1 libxfixes3"
            )
            return None
        logger.debug(f"Browser OK ({label}): {path}")
        return path

    # 1. Explicit user override — trust it unconditionally
    env_exe = os.environ.get("REMOTION_CHROME_EXECUTABLE", "")
    if env_exe and Path(env_exe).is_file():
        logger.debug(f"Browser from env REMOTION_CHROME_EXECUTABLE: {env_exe}")
        return env_exe

    # 2 & 3. Playwright-managed browsers (headless_shell first, then full chromium).
    # Playwright installs dependencies when you run 'npx playwright install chromium',
    # making these the most reliable choice on a fresh machine.
    pw_roots = [
        Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")),
        Path("/opt/pw-browsers"),
        Path.home() / ".cache" / "ms-playwright",
    ]
    for pw_root in pw_roots:
        if not pw_root.is_dir():
            continue
        for hs in sorted(pw_root.glob("chromium_headless_shell-*/chrome-linux/headless_shell"),
                         reverse=True):
            p = _accept(str(hs), "Playwright headless_shell")
            if p:
                return p
        for ch in sorted(pw_root.glob("chromium-*/chrome-linux/chrome"), reverse=True):
            p = _accept(str(ch), "Playwright chromium")
            if p:
                return p

    # 4. Remotion's own downloaded browser — may need extra system libs
    remotion_cache_dir = _REMOTION_DIR / "node_modules" / ".remotion"
    for binary_name in ("chrome-headless-shell", "headless_shell"):
        for candidate in sorted(remotion_cache_dir.rglob(binary_name), reverse=True):
            if candidate.is_file():
                p = _accept(str(candidate), f"Remotion cache {binary_name}")
                if p:
                    return p

    # 5. System headless-shell binaries
    for name in ("chrome-headless-shell", "chromium-headless-shell", "headless_shell"):
        raw = shutil.which(name)
        if raw:
            p = _accept(raw, f"system {name}")
            if p:
                return p

    # 6. Full Chrome/Chromium — skip snap wrapper scripts
    for name in ("chromium-browser", "chromium", "google-chrome-stable", "google-chrome"):
        raw = shutil.which(name)
        if raw:
            p = _accept(raw, f"system {name}")
            if p:
                return p

    return None


def _ensure_remotion(on_progress=None) -> bool:
    """
    Check that Node.js is present, that Remotion packages are installed,
    and that a compatible headless browser is available.
    Runs 'npm install' automatically on first call (takes ~60 s).
    Returns True only when everything is ready.
    """
    if not shutil.which("node"):
        logger.warning(
            "Node.js not found — Remotion unavailable. "
            "Install: sudo apt-get install -y nodejs npm"
        )
        return False
    if not (_REMOTION_DIR / "package.json").exists():
        logger.debug("remotion/package.json missing — skipping Remotion")
        return False

    remotion_bin = _REMOTION_DIR / "node_modules" / ".bin" / "remotion"
    if not remotion_bin.exists():
        logger.info("Installing Remotion packages (first run — ~60 s)…")
        if on_progress:
            on_progress("Installing Remotion (first run, ~60 s)…")
        try:
            r = subprocess.run(
                ["npm", "install"],
                cwd=_REMOTION_DIR,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if r.returncode != 0:
                logger.warning(f"npm install failed:\n{r.stderr[-800:]}")
                return False
            logger.info("Remotion packages installed.")
        except Exception as exc:
            logger.warning(f"npm install error: {exc}")
            return False

    if not remotion_bin.exists():
        logger.warning("Remotion binary not found after npm install")
        return False

    browser = _find_headless_browser()
    if not browser:
        # Auto-download Remotion's own headless browser
        logger.info("No headless browser found — downloading via 'remotion browser ensure'…")
        if on_progress:
            on_progress("Downloading headless browser (first run, ~60 s)…")
        try:
            r = subprocess.run(
                [str(remotion_bin), "browser", "ensure"],
                cwd=_REMOTION_DIR,
                capture_output=True,
                text=True,
                timeout=180,
            )
            combined_out = (r.stdout or "") + (r.stderr or "")
            if r.returncode == 0:
                logger.info("Remotion browser downloaded successfully.")
            else:
                logger.warning(f"remotion browser ensure exited {r.returncode}: {combined_out[-400:]}")
            # Re-scan even on non-zero exit — partial downloads can still work
            browser = _find_headless_browser()
        except Exception as exc:
            logger.warning(f"remotion browser ensure failed: {exc}")

    if not browser:
        logger.warning(
            "No headless browser found for Remotion.\n"
            "Fix (pick ONE — run from the project directory):\n"
            "  npx playwright install chromium          ← recommended\n"
            "  OR: snap install chromium                ← Ubuntu snap\n"
            "  OR: set REMOTION_CHROME_EXECUTABLE=/path/to/chrome in .env\n"
            "Note: 'sudo apt-get install chromium-browser' installs a snap wrapper\n"
            "      that does NOT work — use npx playwright install chromium instead."
        )
        return False

    return True


def _render_with_remotion(
    clip_paths: list[Path],
    title: str,
    output_path: Path,
    config,
    labels: list[str],
    viral_scores: list[int] | None = None,
    on_progress=None,
) -> Path | None:
    """
    Render the ranking video with Remotion (React + headless Chrome).
    Returns the output Path on success, None if Remotion is unavailable or fails
    (the caller will then fall back to the ffmpeg pipeline).
    """
    if not _ensure_remotion(on_progress):
        return None

    fps               = 30
    clip_dur_frames   = config.clip_duration * fps
    n                 = len(clip_paths)
    session_id        = uuid.uuid4().hex[:10]
    clips_public      = _REMOTION_DIR / "public" / "clips" / session_id
    clips_public.mkdir(parents=True, exist_ok=True)

    # Expose ding SFX to Remotion's static server (generate if missing)
    sfx_public = _REMOTION_DIR / "public" / "sfx"
    sfx_public.mkdir(parents=True, exist_ok=True)
    has_ding   = False
    ding_src   = _get_ding()
    if ding_src and ding_src.exists():
        sfx_dest = sfx_public / "ding.mp3"
        if not sfx_dest.exists():
            shutil.copy2(ding_src, sfx_dest)
        has_ding = sfx_dest.exists()

    props_file = _REMOTION_DIR / f"_props_{session_id}.json"

    try:
        # Copy source clips into public/clips/{session_id}/ and probe durations.
        # Remotion bundles public/ into a temp webpack dir and does NOT follow
        # symlinks, so we must copy rather than symlink.
        clip_refs  = []
        scores     = viral_scores or [0] * n
        total_frames_actual = 0

        for i, (path, label) in enumerate(zip(clip_paths, labels)):
            dest_name = f"clip_{i}.mp4"
            dest      = clips_public / dest_name

            # Scale clips to 720×1280 before giving them to Remotion.
            # The Remotion compositor (Rust) crashes with SIGABRT when decoding
            # full-resolution 1080×1920 clips for 3+ sequential videos — its
            # frame buffer overflows.  720×1280 uses 56% less memory per frame
            # and always renders cleanly.  The React composition upscales back
            # to 1080×1920 via object-fit:cover, so output quality is unchanged.
            try:
                _ffmpeg(
                    "-i", str(path),
                    "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
                    "-c:v", VIDEO_CODEC, "-crf", "20", "-preset", "fast",
                    "-c:a", "aac", "-b:a", "128k",
                    str(dest),
                )
                logger.debug(f"  Scaled clip {i} to 720×1280 for Remotion")
            except Exception as scale_err:
                logger.warning(f"  Scale failed for clip {i}: {scale_err} — copying original")
                try:
                    shutil.copy2(path, dest)
                except Exception as copy_err:
                    logger.error(
                        f"  Clip {i} is missing or unreadable — aborting render.\n"
                        f"  Source path: {path}\n"
                        f"  Scale error: {scale_err}\n"
                        f"  Copy error:  {copy_err}"
                    )
                    if on_progress:
                        on_progress(f"Clip {i + 1} missing: {path.name}")
                    return None   # caller raises RuntimeError with full checklist

            if not dest.exists() or dest.stat().st_size == 0:
                logger.error(f"  Clip {i} file missing after scale/copy: {dest}")
                if on_progress:
                    on_progress(f"Clip {i + 1} empty after processing: {dest.name}")
                return None

            # Use the actual clip duration so Remotion mirrors the source video's
            # natural pacing. The duration gate in scraper ensures the source is
            # ≤65s, so clips from it will naturally sum to ≤65s total.
            actual_dur = _probe_duration(dest)
            if actual_dur and actual_dur > 0:
                dur_frames = int(round(actual_dur * fps))
            else:
                dur_frames = clip_dur_frames
            total_frames_actual += dur_frames

            clip_rank = n - i   # rank 1 = best (last shown), rank n = first shown
            clip_refs.append({
                "path":           f"{session_id}/{dest_name}",
                "rank":           clip_rank,
                "label":          _make_short_label(label, rank=clip_rank, n_clips=n),
                "durationFrames": dur_frames,
                "viralScore":     scores[i] if i < len(scores) else 0,
            })

        # Enable 3-2-1 countdown before the #1 reveal whenever we have 5+ clips
        has_countdown = n >= 5

        props = {
            "clips":        clip_refs,
            "title":        title,
            "watermark":    getattr(config, "watermark_text", "@CatCentral"),
            "totalFrames":  total_frames_actual,
            "hasDing":      has_ding,
            "hasCountdown": has_countdown,
        }
        props_file.write_text(_json.dumps(props))

        output_path.parent.mkdir(parents=True, exist_ok=True)

        total_secs = total_frames_actual / fps
        if on_progress:
            on_progress(f"Rendering with Remotion ({total_secs:.0f}s video)…")
        concurrency = min(4, multiprocessing.cpu_count())
        logger.info(
            f"Remotion render: {n} clips, {total_frames_actual} frames "
            f"({total_secs:.0f}s), concurrency={concurrency}"
        )

        remotion_bin = _REMOTION_DIR / "node_modules" / ".bin" / "remotion"
        cmd = [
            str(remotion_bin), "render",
            "src/index.tsx",
            "CatRanking",
            str(output_path.resolve()),
            f"--props={props_file.resolve()}",
            "--codec=h264",
            "--crf=18",
            "--log=verbose",
            "--overwrite",
            f"--concurrency={concurrency}",
            # swangle = software WebGL — works without a GPU or display server.
            "--gl=swangle",
            # Disable the native Rust compositor which crashes (SIGABRT) on some
            # headless Linux systems when combined with swangle rendering.
            # Pure-Chrome rendering is slightly slower but always stable.
            "--disable-compositor",
        ]

        # Pass the headless browser explicitly so Remotion never tries to
        # auto-download one (which requires internet access and can time out).
        browser_exe = _find_headless_browser()
        if browser_exe:
            cmd.append(f"--browser-executable={browser_exe}")
            logger.info(f"Remotion using browser: {browser_exe}")
        else:
            logger.error(
                "No headless browser found for Remotion. "
                "Run: npx playwright install chromium  OR  "
                "sudo apt-get install -y chromium"
            )
            return None

        logger.info(f"Remotion command: {' '.join(cmd[:6])} …")
        result = subprocess.run(
            cmd,
            cwd=_REMOTION_DIR,
            capture_output=True,
            text=True,
            timeout=1800,   # 30 min — generous for slow swangle renders
        )

        # Always log Remotion output — at INFO on success, WARNING on failure
        # so it's visible without needing LOG_LEVEL=DEBUG.
        combined = ((result.stderr or "") + (result.stdout or "")).strip()
        if combined:
            if result.returncode == 0:
                logger.info(f"Remotion output (last 2KB):\n{combined[-2000:]}")
            else:
                logger.warning(f"Remotion output (last 2KB):\n{combined[-2000:]}")

        if result.returncode != 0:
            err = combined[-1200:].strip()
            logger.error(f"Remotion render failed (rc={result.returncode})\n{err}")
            if on_progress:
                first_err = next(
                    (l.strip() for l in err.splitlines() if l.strip() and not l.startswith("[")),
                    err[:160],
                )
                on_progress(f"Remotion error: {first_err}")
            return None

        if output_path.exists() and output_path.stat().st_size > 50_000:
            logger.info(f"Remotion render complete → {output_path}")
            return output_path

        logger.error(
            f"Remotion returned rc=0 but output is missing or empty: {output_path}"
        )
        return None

    except subprocess.TimeoutExpired:
        logger.error("Remotion render timed out (>30 min) — consider reducing clip count")
        return None
    except Exception as exc:
        logger.error(f"Remotion render error: {exc}", exc_info=True)
        return None
    finally:
        props_file.unlink(missing_ok=True)
        shutil.rmtree(clips_public, ignore_errors=True)


# ── Main public function ──────────────────────────────────────────────────────

def create_ranking_video(
    clip_paths: list[Path],
    title: str,
    output_path: Path,
    config,
    clip_platforms: list[str] | None = None,
    on_progress=None,
    clip_labels: list[str] | None = None,
    viral_scores: list[int] | None = None,
) -> Path:
    """
    Build a ranking-style Shorts video from cat clips using Remotion.

    Remotion renders an animated React overlay (rank numbers, title bar,
    viral badge, countdown, watermark) over the source clips.

    Raises RuntimeError if Remotion is unavailable or the render fails —
    fix the Remotion setup rather than falling back silently to a plain render.
    """
    if len(clip_paths) < 2:
        raise ValueError(f"Need at least 2 clips, got {len(clip_paths)}")

    n         = len(clip_paths)
    platforms = clip_platforms or ["unknown"] * n
    labels    = clip_labels    or [f"CLIP {i + 1}" for i in range(n)]

    def _step(msg: str) -> None:
        logger.debug(msg)
        if on_progress:
            on_progress(msg)

    # ── Pre-blur clips that carry a burned-in overlay (ranking_slice, TikTok…) ─
    with tempfile.TemporaryDirectory(prefix="catcentral_preblur_") as blurtmp:
        btmp  = Path(blurtmp)
        ready: list[Path] = []
        for idx, (src, platform) in enumerate(zip(clip_paths, platforms)):
            regions = _PLATFORM_BLUR_REGIONS.get(platform, _PLATFORM_BLUR_REGIONS["unknown"])
            if regions:
                _step(f"Removing {platform} watermark — clip {idx + 1}/{n}…")
                dst = btmp / f"blur_{idx}.mp4"
                _blur_source_watermarks(src, dst, platform)
                ready.append(dst)
            else:
                ready.append(src)

        out = _render_with_remotion(
            ready, title, output_path, config, labels,
            viral_scores=viral_scores, on_progress=on_progress,
        )

    if out:
        logger.info(f"Ranking video created (Remotion): {output_path}")
        return out

    # Remotion failed — surface a clear error so the user can fix the setup.
    raise RuntimeError(
        "Remotion render failed — video not created.\n"
        "Checklist:\n"
        "  1. Node.js installed?       node --version\n"
        "  2. npm packages installed?  cd remotion && npm install\n"
        "  3. Headless browser found?  check REMOTION_CHROME_EXECUTABLE env var\n"
        "     or run: npx playwright install chromium\n"
        "  4. Check the log file for the full Remotion error output.\n"
        "Tip: set LOG_LEVEL=DEBUG in .env for verbose Remotion logs."
    )


# ── Full-short mode functions ─────────────────────────────────────────────────

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
            "[v2]crop=1080:160:0:0,boxblur=30:6[btop];"
            # Bottom 220px — username, music, action buttons
            "[v3]crop=1080:220:0:1700,boxblur=30:6[bbot];"
            # Left 70px strip — side rank number panels
            "[v4]crop=70:1920:0:0,boxblur=30:6[bleft];"
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

    if on_progress:
        on_progress("Blurring original text overlays…")
    blurred = source_path.with_name(f"_blurred_{source_path.stem}.mp4")
    _blur_text_regions(source_path, blurred)

    if on_progress:
        on_progress("Adding CatCentral title and watermark…")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = _add_branding(blurred, output_path, title, watermark_text)
    blurred.unlink(missing_ok=True)

    if on_progress:
        on_progress("Done — video ready.")

    if not result or not result.exists():
        raise RuntimeError("Branding step failed for full-short mode")


def _render_full_short_with_remotion(
    blurred_source: Path,
    rank_segments: list[dict],
    title: str,
    output_path: Path,
    config,
    on_progress=None,
) -> Path | None:
    """Render the full-short ranking video with Remotion."""
    if not _ensure_remotion(on_progress):
        return None

    fps        = FPS
    session_id = uuid.uuid4().hex[:10]
    clips_pub  = _REMOTION_DIR / "public" / "clips" / session_id
    clips_pub.mkdir(parents=True, exist_ok=True)

    # Copy blurred source into Remotion public/
    dest = clips_pub / "source.mp4"
    shutil.copy2(blurred_source, dest)

    # Ding SFX
    sfx_public = _REMOTION_DIR / "public" / "sfx"
    sfx_public.mkdir(parents=True, exist_ok=True)
    has_ding = False
    ding_src = _get_ding()
    if ding_src and ding_src.exists():
        sfx_dest = sfx_public / "ding.mp3"
        if not sfx_dest.exists():
            shutil.copy2(ding_src, sfx_dest)
        has_ding = sfx_dest.exists()

    props_file = _REMOTION_DIR / f"_props_{session_id}.json"

    try:
        n   = len(rank_segments)

        clip_refs = []
        total_frames = 0
        for i, seg in enumerate(rank_segments):
            start_f  = int(round(seg.get("start_time", 0) * fps))
            dur_f    = max(fps, int(round((seg.get("end_time", 0) - seg.get("start_time", 0)) * fps)))
            raw_label = seg.get("screen_label") or seg.get("title") or ""
            clip_rank = n - i
            clip_refs.append({
                "path":           f"{session_id}/source.mp4",
                "rank":           clip_rank,
                "label":          _make_short_label(raw_label, rank=clip_rank, n_clips=n),
                "durationFrames": dur_f,
                "startFrom":      start_f,
                "viralScore":     seg.get("_viral_score", 0),
            })
            total_frames += dur_f

        props = {
            "clips":        clip_refs,
            "title":        title,
            "watermark":    getattr(config, "watermark_text", "@CatCentral"),
            "totalFrames":  total_frames,
            "hasDing":      has_ding,
            "hasCountdown": n >= 5,
        }
        props_file.write_text(_json.dumps(props))
        output_path.parent.mkdir(parents=True, exist_ok=True)

        total_secs = total_frames / fps
        if on_progress:
            on_progress(f"Rendering with Remotion ({total_secs:.0f}s video)…")

        concurrency = min(4, multiprocessing.cpu_count())
        remotion_bin = _REMOTION_DIR / "node_modules" / ".bin" / "remotion"
        browser_exe = _find_headless_browser()
        cmd = [
            str(remotion_bin), "render",
            "src/index.tsx",
            "CatRanking",
            str(output_path.resolve()),
            f"--props={props_file.resolve()}",
            "--codec=h264",
            "--crf=18",
            "--log=verbose",
            "--overwrite",
            f"--concurrency={concurrency}",
            "--gl=swangle",
            "--disable-compositor",
        ]
        if browser_exe:
            cmd.append(f"--browser-executable={browser_exe}")

        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(_REMOTION_DIR), timeout=1800,
        )
        if result.returncode != 0:
            logger.error(f"Remotion render failed:\n{result.stderr[-2000:]}")
            return None

        return output_path if output_path.exists() else None

    finally:
        props_file.unlink(missing_ok=True)
        shutil.rmtree(clips_pub, ignore_errors=True)


# ── CLI helper ────────────────────────────────────────────────────────────────

def check_ffmpeg():
    """Raise RuntimeError if ffmpeg or ffprobe is not installed."""
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError(
                f"{tool} is not installed or not on PATH.\n"
                "Install: sudo apt install ffmpeg"
            )
