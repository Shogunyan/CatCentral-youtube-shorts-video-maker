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
import logging
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

_WOOSH_PATH = Path(__file__).parent.parent / "assets" / "sfx" / "woosh.mp3"


def _get_woosh() -> Path | None:
    """Return path to the woosh sound, generating it with ffmpeg if needed."""
    if _WOOSH_PATH.exists():
        return _WOOSH_PATH
    try:
        _WOOSH_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Rising chirp: frequency sweeps from ~120 Hz to ~1800 Hz over 0.45s
        # Phase formula: sin(2π·(f0·t + (f1-f0)/(2T)·t²))
        # (f1-f0)/(2T) = (1680)/(0.9) ≈ 1867
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi",
                "-i", (
                    "aevalsrc="
                    "0.45*sin(6.283*(120*t+1867*t*t))"
                    "+0.2*sin(6.283*(240*t+3733*t*t))"
                    ":s=44100:c=stereo:d=0.45"
                ),
                "-af", "afade=t=in:d=0.02,afade=t=out:st=0.36:d=0.09,volume=2.5",
                str(_WOOSH_PATH),
            ],
            check=True,
            capture_output=True,
        )
        logger.info(f"Generated woosh SFX → {_WOOSH_PATH}")
        return _WOOSH_PATH
    except Exception as e:
        logger.warning(f"Could not generate woosh sound: {e}")
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


def _add_woosh_to_clip(
    video_path: Path,
    woosh_path: Path,
    output_path: Path,
) -> Path:
    """
    Mix a woosh sound effect at the very start of a clip.
    Handles clips that have no original audio track.
    """
    if _has_audio(video_path):
        audio_fc = (
            "[0:a]volume=1.0[orig];"
            "[1:a]volume=2.0[w];"
            "[orig][w]amix=inputs=2:duration=first:normalize=0[a]"
        )
    else:
        audio_fc = "[1:a]volume=2.0[a]"

    _ffmpeg(
        "-i", str(video_path),
        "-i", str(woosh_path),
        "-filter_complex", audio_fc,
        "-map", "0:v",
        "-map", "[a]",
        "-c:v", "copy",
        "-c:a", AUDIO_CODEC,
        "-b:a", AUDIO_BITRATE,
        "-ar", "44100",
        "-ac", "2",
        str(output_path),
    )
    return output_path


# ── Ranking overlay ───────────────────────────────────────────────────────────

def _make_short_label(title: str) -> str:
    """Turn a clip title into a short ALL-CAPS label (≤16 chars)."""
    # Strip hashtags, URLs, and common filler
    label = re.sub(r"#\w+", "", title).strip()
    label = re.sub(r"https?://\S+", "", label).strip()
    # Take first 4 words, cap at 16 chars
    words = label.split()[:4]
    return " ".join(words)[:16].upper() or "CAT CLIP"


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
    parts.append("drawbox=x=0:y=125:w=360:h=1700:color=black@0.52:t=fill")

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
            num_size  = 84
            lbl_color = "#FFFFFF"
            lbl_size  = 40
        elif is_past:
            num_color = "white@0.55"
            num_size  = 54
            lbl_color = "white@0.55"
            lbl_size  = 28
        else:
            num_color = "white@0.28"
            num_size  = 54
            lbl_color = "white@0.28"
            lbl_size  = 28

        # Rank number
        num_str = _escape_drawtext(f"{rank}.")
        parts.append(
            f"drawtext=text='{num_str}'{_FONT_B}"
            f":fontsize={num_size}:fontcolor={num_color}"
            f":borderw=3:bordercolor=black@0.9"
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
            f":borderw=2:bordercolor=black@0.85"
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
    scale_crop = (
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
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
    w, h = result.stdout.strip().split(",")
    return int(w), int(h)


def _resolve_region(region: tuple, vw: int, vh: int) -> tuple[int, int, int, int]:
    cx_expr, cy_expr, bw, bh = region
    cx = int(eval(cx_expr.replace("iw", str(vw)).replace("ih", str(vh))))  # noqa: S307
    cy = int(eval(cy_expr.replace("iw", str(vw)).replace("ih", str(vh))))  # noqa: S307
    cx = max(0, min(cx, vw - bw))
    cy = max(0, min(cy, vh - bh))
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


# ── Main public function ──────────────────────────────────────────────────────

def create_ranking_video(
    clip_paths: list[Path],
    title: str,
    output_path: Path,
    config,
    clip_platforms: list[str] | None = None,
    on_progress=None,
    tts_audio: dict | None = None,   # kept for API compat — ignored
    clip_labels: list[str] | None = None,
) -> Path:
    """
    Build a ranking-style Shorts video from cat clips.

    Format: jumps straight into clip #N with the full ranking list overlay
    on the left side of every frame. No intro card. No TTS.
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

    clip_duration = config.clip_duration

    with tempfile.TemporaryDirectory(prefix="catcentral_") as tmpdir:
        tmp = Path(tmpdir)
        processed: list[Path] = []

        # Pre-load woosh sound (generated once, reused for every transition)
        woosh = _get_woosh()
        if woosh:
            _step("Woosh SFX ready…")

        # ── 1. Process each clip ─────────────────────────────────────────────
        for idx, src in enumerate(clip_paths):
            rank     = n - idx   # n=5,idx=0 → rank 5 first; idx=4 → rank 1 last
            step1    = tmp / f"rank{rank}.mp4"
            platform = platforms[idx]

            blur_regions = _PLATFORM_BLUR_REGIONS.get(
                platform, _PLATFORM_BLUR_REGIONS["unknown"]
            )
            if blur_regions:
                _step(f"Removing {platform} watermark — clip {idx + 1}/{n}…")
                blurred = tmp / f"blur_rank{rank}.mp4"
                _blur_source_watermarks(src, blurred, platform)
                source = blurred
            else:
                source = src

            _step(f"Processing clip {idx + 1}/{n}  (rank #{rank})…")
            _process_clip(
                source, step1, rank, clip_duration, title,
                all_labels=labels,
                current_idx=idx,
                n=n,
            )

            # Add woosh at the start of every clip (signals "new clip incoming")
            if woosh:
                woosh_out = tmp / f"woosh_rank{rank}.mp4"
                try:
                    _add_woosh_to_clip(step1, woosh, woosh_out)
                    processed.append(woosh_out)
                except Exception as e:
                    logger.warning(f"Woosh mix failed for rank {rank}: {e}")
                    processed.append(step1)
            else:
                processed.append(step1)

        # ── 2. Concatenate ───────────────────────────────────────────────────
        _step("Concatenating all clips…")
        joined = tmp / "joined.mp4"
        _concat_clips(processed, joined)

        # ── 3. Add watermark ─────────────────────────────────────────────────
        _step(f"Adding {config.watermark_text} watermark…")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _add_watermark(joined, output_path, config.watermark_text)

    logger.info(f"Ranking video created: {output_path}")
    return output_path


# ── CLI helper ────────────────────────────────────────────────────────────────

def check_ffmpeg():
    """Raise RuntimeError if ffmpeg or ffprobe is not installed."""
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError(
                f"{tool} is not installed or not on PATH.\n"
                "Install: sudo apt install ffmpeg"
            )
