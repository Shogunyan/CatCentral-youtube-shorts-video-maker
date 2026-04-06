"""
video_editor.py — Builds a ranking-style YouTube Shorts video from 5 cat clips.

Pipeline (all via ffmpeg subprocess):
  1. Process each clip: scale → crop → trim → rank-number overlay
  2. Generate a title card (lavfi black + drawtext)
  3. Concatenate: title card + clips 5→1
  4. Overlay persistent title text at top + moving @CatCentral watermark
  5. Encode final MP4 (H.264 / AAC, 1080×1920)
"""
import logging
import random
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
VIDEO_CRF = "23"

# Rank overlay — shown for this many seconds at the start of each clip
RANK_SHOW_SECS = 2.5


def _ffmpeg(*args, check=True) -> subprocess.CompletedProcess:
    """Run ffmpeg with the given arguments, suppressing most output."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    logger.debug("ffmpeg: " + " ".join(str(a) for a in cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr}")
    return result


def _escape_drawtext(text: str) -> str:
    """Escape special chars for ffmpeg drawtext filter."""
    for ch in ("\\", ":", "'", "[", "]"):
        text = text.replace(ch, "\\" + ch)
    return text


def _find_font() -> str:
    """Return a font path that ffmpeg can use for drawtext."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",  # macOS
        "C:/Windows/Fonts/arialbd.ttf",          # Windows
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return ""  # let ffmpeg use its default


FONT = _find_font()
_FONT_OPT = f":fontfile={FONT}" if FONT else ""


# ── Step 1: Process individual clips ─────────────────────────────────────────

def _process_clip(
    input_path: Path,
    output_path: Path,
    rank: int,
    clip_duration: int,
    title: str,
) -> Path:
    """
    Resize/crop clip to 1080×1920, trim to clip_duration, add rank overlay.
    """
    rank_text = _escape_drawtext(f"#{rank}")
    title_text = _escape_drawtext(title)

    rank_filter = (
        f"drawtext=text='{rank_text}'{_FONT_OPT}"
        f":fontsize=220:fontcolor=white"
        f":borderw=10:bordercolor=black"
        f":x=(w-tw)/2:y=(h-th)/2"
        f":box=1:boxcolor=black@0.35:boxborderw=20"
        f":enable='between(t,0,{RANK_SHOW_SECS})'"
    )

    title_filter = (
        f"drawtext=text='{title_text}'{_FONT_OPT}"
        f":fontsize=48:fontcolor=white"
        f":borderw=4:bordercolor=black"
        f":x=(w-tw)/2:y=55"
        f":box=1:boxcolor=black@0.5:boxborderw=14"
    )

    scale_crop = (
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_W}:{TARGET_H}"
    )

    vf = f"{scale_crop},fps={FPS},{rank_filter},{title_filter}"

    _ffmpeg(
        "-i", str(input_path),
        "-t", str(clip_duration),
        "-vf", vf,
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


# ── Step 2: Title card ────────────────────────────────────────────────────────

def _make_title_card(output_path: Path, title: str, duration: int = 2) -> Path:
    """Create a black title card with the ranking title centered."""
    title_text = _escape_drawtext(title)
    subtitle_text = _escape_drawtext("Ranking 5 → 1")

    vf = (
        f"drawtext=text='{title_text}'{_FONT_OPT}"
        f":fontsize=72:fontcolor=white"
        f":borderw=5:bordercolor=black"
        f":x=(w-tw)/2:y=(h-th)/2-60,"
        f"drawtext=text='{subtitle_text}'{_FONT_OPT}"
        f":fontsize=46:fontcolor=yellow"
        f":borderw=3:bordercolor=black"
        f":x=(w-tw)/2:y=(h-th)/2+60"
    )

    _ffmpeg(
        "-f", "lavfi",
        "-i", f"color=c=black:size={TARGET_W}x{TARGET_H}:rate={FPS}",
        "-f", "lavfi",
        "-i", "anullsrc=r=44100:cl=stereo",
        "-t", str(duration),
        "-vf", vf,
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


# ── Step 3: Concatenate ───────────────────────────────────────────────────────

def _concat_clips(clip_paths: list[Path], output_path: Path) -> Path:
    """Losslessly concatenate pre-encoded clips via the concat demuxer."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        for p in clip_paths:
            f.write(f"file '{str(p)}'\n")
        list_file = Path(f.name)

    _ffmpeg(
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(output_path),
    )
    list_file.unlink(missing_ok=True)
    return output_path


# ── Step 4: Add moving watermark ─────────────────────────────────────────────

def _add_watermark(input_path: Path, output_path: Path, watermark_text: str) -> Path:
    """
    Burn a moving semi-transparent @CatCentral watermark into the video.

    The watermark cycles through 4 corners every 12 seconds so it never stays
    in one place long enough to be distracting but always identifies the channel.
    """
    wm = _escape_drawtext(watermark_text)

    # Corner cycle: top-left → top-right → bottom-left → bottom-right
    # Using ffmpeg conditional expressions based on time segment
    pad = 55  # px from edge
    x_expr = (
        f"if(eq(mod(floor(t/12),4),0),{pad},"
        f"if(eq(mod(floor(t/12),4),1),w-tw-{pad},"
        f"if(eq(mod(floor(t/12),4),2),{pad},"
        f"w-tw-{pad})))"
    )
    y_expr = (
        f"if(eq(mod(floor(t/12),4),0),{pad + 20},"
        f"if(eq(mod(floor(t/12),4),1),{pad + 20},"
        f"if(eq(mod(floor(t/12),4),2),h-th-{pad},"
        f"h-th-{pad})))"
    )

    wm_filter = (
        f"drawtext=text='{wm}'{_FONT_OPT}"
        f":fontsize=34:fontcolor=white@0.75"
        f":borderw=2:bordercolor=black@0.6"
        f":x='{x_expr}':y='{y_expr}'"
    )

    _ffmpeg(
        "-i", str(input_path),
        "-vf", wm_filter,
        "-c:v", VIDEO_CODEC,
        "-crf", VIDEO_CRF,
        "-preset", "fast",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    )
    return output_path


# ── Optional: Blur source-video watermarks ───────────────────────────────────

def _blur_corner_watermarks(input_path: Path, output_path: Path) -> Path:
    """
    Apply a gentle Gaussian blur to all four corners of the video to reduce
    the visibility of any platform watermarks in source clips.
    Each corner box is 220×120 px at 1080×1920 resolution.
    """
    bw, bh = 220, 120  # blur region dimensions
    blur_strength = 12  # Gaussian blur sigma

    # Build a split/blur/overlay chain for 4 corners
    vf = (
        f"[in]split=5[base][c1][c2][c3][c4];"
        f"[c1]crop={bw}:{bh}:0:0,gblur=sigma={blur_strength}[b1];"
        f"[c2]crop={bw}:{bh}:{TARGET_W - bw}:0,gblur=sigma={blur_strength}[b2];"
        f"[c3]crop={bw}:{bh}:0:{TARGET_H - bh},gblur=sigma={blur_strength}[b3];"
        f"[c4]crop={bw}:{bh}:{TARGET_W - bw}:{TARGET_H - bh},gblur=sigma={blur_strength}[b4];"
        f"[base][b1]overlay=0:0[o1];"
        f"[o1][b2]overlay={TARGET_W - bw}:0[o2];"
        f"[o2][b3]overlay=0:{TARGET_H - bh}[o3];"
        f"[o3][b4]overlay={TARGET_W - bw}:{TARGET_H - bh}[out]"
    )

    try:
        _ffmpeg(
            "-i", str(input_path),
            "-filter_complex", vf,
            "-map", "[out]",
            "-map", "0:a?",
            "-c:v", VIDEO_CODEC,
            "-crf", VIDEO_CRF,
            "-preset", "fast",
            "-c:a", "copy",
            str(output_path),
        )
        return output_path
    except Exception as e:
        # Non-fatal: if blur fails, continue with original
        logger.warning(f"Corner blur failed (non-fatal): {e}")
        shutil.copy2(input_path, output_path)
        return output_path


# ── Main public function ──────────────────────────────────────────────────────

def create_ranking_video(
    clip_paths: list[Path],
    title: str,
    output_path: Path,
    config,
    blur_source_watermarks: bool = True,
) -> Path:
    """
    Build a ranking-style Shorts video from exactly `config.clips_per_video` clips.

    Args:
        clip_paths:  Local video file paths. clip_paths[0] will be ranked #5,
                     clip_paths[-1] will be ranked #1 (randomly ordered by caller).
        title:       Short title shown at top of every clip and on the title card.
        output_path: Where to write the final MP4.
        config:      Config object (for clip_duration, watermark_text, etc.).
        blur_source_watermarks: Apply corner blur to reduce platform watermarks.
    """
    if len(clip_paths) < 2:
        raise ValueError(f"Need at least 2 clips, got {len(clip_paths)}")

    n = len(clip_paths)
    clip_duration = config.clip_duration

    with tempfile.TemporaryDirectory(prefix="catcentral_") as tmpdir:
        tmp = Path(tmpdir)

        # ── 1. Process each clip (resize + rank overlay) ─────────────────────
        processed: list[Path] = []

        for idx, src in enumerate(clip_paths):
            rank = n - idx  # n=5→rank 5 first, idx=4→rank 1 last
            step1 = tmp / f"step1_rank{rank}.mp4"

            if blur_source_watermarks:
                blurred = tmp / f"blurred_rank{rank}.mp4"
                _blur_corner_watermarks(src, blurred)
                _process_clip(blurred, step1, rank, clip_duration, title)
            else:
                _process_clip(src, step1, rank, clip_duration, title)

            processed.append(step1)
            logger.debug(f"  Processed rank #{rank} clip")

        # ── 2. Title card ─────────────────────────────────────────────────────
        title_card = tmp / "title_card.mp4"
        _make_title_card(title_card, title, duration=2)

        # ── 3. Concatenate: title card first, then rank 5→1 ──────────────────
        concat_in = [title_card] + processed
        joined = tmp / "joined.mp4"
        _concat_clips(concat_in, joined)

        # ── 4. Add moving @CatCentral watermark ──────────────────────────────
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _add_watermark(joined, output_path, config.watermark_text)

    logger.info(f"Ranking video created: {output_path}")
    return output_path


# ── CLI helper ────────────────────────────────────────────────────────────────

def check_ffmpeg():
    """Raise RuntimeError if ffmpeg is not installed."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            "ffmpeg is not installed or not on PATH.\n"
            "Install it with:  sudo apt install ffmpeg  (Ubuntu/Debian)\n"
            "                  brew install ffmpeg       (macOS)"
        )
