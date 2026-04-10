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
import re
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

_WOOSH_PATH = Path(__file__).parent.parent / "assets" / "sfx" / "woosh.mp3"


_WOOSH_VERSION = 2   # bump to force regeneration when synthesis changes


def _get_woosh() -> Path | None:
    """Return path to the woosh sound, generating it with ffmpeg if needed."""
    ver_file = _WOOSH_PATH.with_suffix(".ver")
    needs_regen = (
        not _WOOSH_PATH.exists()
        or not ver_file.exists()
        or ver_file.read_text().strip() != str(_WOOSH_VERSION)
    )
    if not needs_regen:
        return _WOOSH_PATH
    try:
        _WOOSH_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Airy whoosh: pink noise band-passed to the 500–3500 Hz "wind" range,
        # amplitude-shaped with a sharp attack and a long tail.
        # The two-stage bandpass removes the harsh high end and the rumbling low
        # end, leaving the breezy mid-range characteristic of a real whoosh SFX.
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi",
                "-i", "anoisesrc=color=pink:duration=0.7:seed=7",
                "-af", (
                    "highpass=f=500,"
                    "lowpass=f=3500,"
                    "afade=t=in:d=0.04,"
                    "afade=t=out:st=0.50:d=0.20,"
                    "volume=5.0"
                ),
                "-ar", "44100",
                "-ac", "2",
                str(_WOOSH_PATH),
            ],
            check=True,
            capture_output=True,
        )
        ver_file.write_text(str(_WOOSH_VERSION))
        logger.info(f"Generated woosh SFX (v{_WOOSH_VERSION}) → {_WOOSH_PATH}")
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
    """Turn a clip title into a 2-word ALL-CAPS sidebar label."""
    # Strip hashtags, URLs, numbers at the start, and punctuation
    label = re.sub(r"#\w+", "", title).strip()
    label = re.sub(r"https?://\S+", "", label).strip()
    label = re.sub(r"^\W+", "", label).strip()
    # Skip filler words so we surface meaningful content words
    FILLER = {
        "the","a","an","of","in","on","at","to","and","or","but","is","it",
        "this","that","my","your","his","her","cat","cats","kitten","funny",
        "video","clip","short","shorts","when","how","why","what","who",
    }
    words = [w for w in label.split() if w.lower() not in FILLER]
    if not words:
        words = label.split()   # fallback: use any words
    # Two words max
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
        ("0", "0",   215, 118),   # title/header bar across the top
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


def _ensure_remotion(on_progress=None) -> bool:
    """
    Check that Node.js is present and that Remotion packages are installed.
    Runs 'npm install' automatically on first call (takes ~60 s).
    Returns True when Remotion is ready.
    """
    if not shutil.which("node"):
        logger.warning(
            "Node.js not found — Remotion unavailable, falling back to ffmpeg. "
            "To enable Remotion, install Node.js: "
            "sudo apt-get install -y nodejs npm"
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
            logger.info("Remotion packages installed successfully.")
        except Exception as exc:
            logger.warning(f"npm install error: {exc}")
            return False
    if not remotion_bin.exists():
        logger.warning("Remotion binary not found after npm install")
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

    # Expose woosh SFX to Remotion's static server
    woosh_src  = _REMOTION_DIR.parent / "assets" / "sfx" / "woosh.mp3"
    sfx_public = _REMOTION_DIR / "public" / "sfx"
    has_woosh  = False
    if woosh_src.exists():
        sfx_public.mkdir(parents=True, exist_ok=True)
        sfx_dest = sfx_public / "woosh.mp3"
        if not sfx_dest.exists():
            try:
                sfx_dest.symlink_to(woosh_src.resolve())
            except Exception:
                shutil.copy2(woosh_src, sfx_dest)
        has_woosh = sfx_dest.exists()

    props_file = _REMOTION_DIR / f"_props_{session_id}.json"

    try:
        # Symlink source clips into public/clips/{session_id}/
        clip_refs = []
        scores = viral_scores or [0] * n
        for i, (path, label) in enumerate(zip(clip_paths, labels)):
            dest_name = f"clip_{i}.mp4"
            dest      = clips_public / dest_name
            try:
                dest.symlink_to(path.resolve())
            except Exception:
                shutil.copy2(path, dest)

            clip_refs.append({
                "path":           f"{session_id}/{dest_name}",
                "rank":           n - i,
                "label":          _make_short_label(label),
                "durationFrames": clip_dur_frames,
                "viralScore":     scores[i] if i < len(scores) else 0,
            })

        # Enable 3-2-1 countdown before the #1 reveal whenever we have 5+ clips
        has_countdown = n >= 5

        props = {
            "clips":        clip_refs,
            "title":        title,
            "watermark":    getattr(config, "watermark_text", "@CatCentral"),
            "totalFrames":  clip_dur_frames * n,
            "hasWoosh":     has_woosh,
            "hasCountdown": has_countdown,
        }
        props_file.write_text(_json.dumps(props))

        output_path.parent.mkdir(parents=True, exist_ok=True)

        if on_progress:
            on_progress("Rendering with Remotion (animated overlay)…")
        logger.info(f"Remotion render: {n} clips × {config.clip_duration}s")

        remotion_bin = _REMOTION_DIR / "node_modules" / ".bin" / "remotion"
        cmd = [
            str(remotion_bin), "render",
            "src/index.tsx",
            "CatRanking",
            str(output_path.resolve()),
            f"--props={props_file.resolve()}",
            "--codec=h264",
            "--crf=18",
            "--log=error",
            "--overwrite",
            "--concurrency=4",
        ]
        result = subprocess.run(
            cmd,
            cwd=_REMOTION_DIR,
            capture_output=True,
            text=True,
            timeout=600,
        )

        if result.returncode != 0:
            err = (result.stderr or result.stdout or "")[-1000:].strip()
            logger.warning(f"Remotion render failed (rc={result.returncode})\n{err}")
            if on_progress:
                # Surface first meaningful error line to the TUI log
                first_err = next(
                    (l.strip() for l in err.splitlines() if l.strip() and not l.startswith("[")),
                    err[:120],
                )
                on_progress(f"Remotion failed: {first_err} — falling back to ffmpeg")
            return None

        if output_path.exists() and output_path.stat().st_size > 50_000:
            logger.info(f"Remotion render complete → {output_path}")
            return output_path

        logger.warning("Remotion output missing or too small")
        return None

    except subprocess.TimeoutExpired:
        logger.warning("Remotion render timed out (>10 min)")
        return None
    except Exception as exc:
        logger.warning(f"Remotion render error: {exc}")
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
    Build a ranking-style Shorts video from cat clips.

    Tries Remotion (React + headless Chrome, animated overlays) first.
    Falls back to the plain ffmpeg pipeline if Remotion is unavailable.
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

    # ── Pre-blur clips that carry a burned-in overlay (ranking_slice, TikTok…) ─
    # Both the Remotion and ffmpeg paths need clean source files.
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

        # ── 1. Try Remotion (animated React overlay) ────────────────────────
        out = _render_with_remotion(
            ready, title, output_path, config, labels,
            viral_scores=viral_scores, on_progress=on_progress,
        )
        if out:
            logger.info(f"Ranking video created (Remotion): {output_path}")
            return out

    # ── 2. FFmpeg fallback ─────────────────────────────────────────────────────
    _step("Using ffmpeg renderer…")
    with tempfile.TemporaryDirectory(prefix="catcentral_") as tmpdir:
        tmp = Path(tmpdir)
        processed: list[Path] = []

        woosh = _get_woosh()
        if woosh:
            _step("Woosh SFX ready…")

        for idx, src in enumerate(clip_paths):
            rank     = n - idx
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

        _step("Concatenating all clips…")
        joined = tmp / "joined.mp4"
        _concat_clips(processed, joined)

        _step(f"Adding {config.watermark_text} watermark…")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _add_watermark(joined, output_path, config.watermark_text)

    logger.info(f"Ranking video created (ffmpeg): {output_path}")
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
