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
    for ch in ("\\", ":", "'", "[", "]", "%"):
        text = text.replace(ch, "\\" + ch)
    return text


def _find_font(bubbly: bool = False) -> str:
    """Return a font path that ffmpeg can use for drawtext."""
    if bubbly:
        # Bubbly/fun font for title overlays
        bubbly_candidates = [
            str(Path(__file__).parent.parent / "assets" / "fonts" / "Fredoka.ttf"),
            "C:/Windows/Fonts/comicbd.ttf",          # Comic Sans Bold (Windows)
        ]
        for p in bubbly_candidates:
            if Path(p).exists():
                return p
    # Fallback to standard bold fonts
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


FONT_BUBBLY = _find_font(bubbly=True)
FONT_PLAIN = _find_font(bubbly=False)
_FONT_BUBBLY_OPT = f":fontfile={FONT_BUBBLY}" if FONT_BUBBLY else ""
_FONT_PLAIN_OPT = f":fontfile={FONT_PLAIN}" if FONT_PLAIN else ""
# Default used for rank numbers / watermark (plain bold)
_FONT_OPT = _FONT_PLAIN_OPT


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

    # Auto-scale title font to fit within frame (max width ~960px with padding)
    title_fontsize = 42
    if len(title) > 40:
        title_fontsize = 34
    if len(title) > 50:
        title_fontsize = 28

    rank_filter = (
        f"drawtext=text='{rank_text}'{_FONT_BUBBLY_OPT}"
        f":fontsize=220:fontcolor=#FFD700"
        f":borderw=10:bordercolor=black"
        f":x=(w-tw)/2:y=(h-th)/2"
        f":box=1:boxcolor=black@0.4:boxborderw=25"
        f":enable='between(t,0,{RANK_SHOW_SECS})'"
    )

    title_filter = (
        f"drawtext=text='{title_text}'{_FONT_BUBBLY_OPT}"
        f":fontsize={title_fontsize}:fontcolor=#00DDFF"
        f":borderw=4:bordercolor=black"
        f":x=(w-tw)/2:y=55"
        f":box=1:boxcolor=black@0.55:boxborderw=14"
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
        "-map", "0:v:0",
        "-map", "0:a:0?",   # optional: some clips may have no audio track
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

def _make_title_card(
    output_path: Path,
    title: str,
    n_clips: int = 5,
    duration: float = 1.5,
    tts_audio: Path | None = None,
) -> Path:
    """
    Create a short, punchy title card with colorful text.
    If `tts_audio` is provided the card length matches the TTS clip.
    """
    title_text = _escape_drawtext(title)
    subtitle_text = _escape_drawtext(f"Ranking {n_clips} → 1")

    # Auto-scale title to fit — two lines if needed
    title_fontsize = 58
    if len(title) > 35:
        title_fontsize = 48
    if len(title) > 45:
        title_fontsize = 40

    vf = (
        f"drawtext=text='{title_text}'{_FONT_BUBBLY_OPT}"
        f":fontsize={title_fontsize}:fontcolor=#00DDFF"
        f":borderw=6:bordercolor=black"
        f":x=(w-tw)/2:y=(h/2)-80,"
        f"drawtext=text='{subtitle_text}'{_FONT_BUBBLY_OPT}"
        f":fontsize=42:fontcolor=#FFD700"
        f":borderw=4:bordercolor=black"
        f":x=(w-tw)/2:y=(h/2)+30"
    )

    if tts_audio and tts_audio.exists():
        # Card length = TTS length; audio = the TTS voice
        _ffmpeg(
            "-f", "lavfi",
            "-i", f"color=c=black:size={TARGET_W}x{TARGET_H}:rate={FPS}",
            "-i", str(tts_audio),
            "-vf", vf,
            "-c:v", VIDEO_CODEC, "-crf", VIDEO_CRF, "-preset", "fast",
            "-c:a", AUDIO_CODEC, "-b:a", AUDIO_BITRATE, "-ar", "44100", "-ac", "2",
            "-shortest",
            "-movflags", "+faststart",
            str(output_path),
        )
    else:
        # Silent title card — short and punchy
        _ffmpeg(
            "-f", "lavfi",
            "-i", f"color=c=black:size={TARGET_W}x{TARGET_H}:rate={FPS}",
            "-f", "lavfi",
            "-i", "anullsrc=r=44100:cl=stereo",
            "-t", str(duration),
            "-vf", vf,
            "-c:v", VIDEO_CODEC, "-crf", VIDEO_CRF, "-preset", "fast",
            "-c:a", AUDIO_CODEC, "-b:a", AUDIO_BITRATE, "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart",
            str(output_path),
        )
    return output_path


# ── TTS mixing helper ─────────────────────────────────────────────────────────

def _mix_tts_into_clip(
    video_path: Path,
    tts_path: Path,
    output_path: Path,
) -> Path:
    """
    Overlay a TTS announcement onto the first few seconds of a clip.

    While the voice speaks, the original audio is ducked to 15%.
    Once the voice finishes, the original audio smoothly restores to 100%.
    """
    from src.tts import get_audio_duration, has_audio_stream

    tts_dur = get_audio_duration(tts_path)
    restore_at = round(tts_dur + 0.35, 2)   # start restoring audio slightly after voice

    orig_exists = has_audio_stream(video_path)

    if orig_exists:
        # Duck original audio while TTS plays, then restore
        audio_fc = (
            f"[0:a]volume='if(lt(t,{restore_at}),0.15,1.0)':eval=frame[orig];"
            f"[1:a]volume=1.6[tts];"
            f"[orig][tts]amix=inputs=2:duration=first:normalize=0[a]"
        )
    else:
        # No original audio — TTS is the only audio track
        audio_fc = "[1:a]volume=1.6[a]"

    _ffmpeg(
        "-i", str(video_path),
        "-i", str(tts_path),
        "-filter_complex", audio_fc,
        "-map", "0:v",
        "-map", "[a]",
        "-c:v", "copy",
        "-c:a", AUDIO_CODEC, "-b:a", AUDIO_BITRATE, "-ar", "44100", "-ac", "2",
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
            # Forward slashes required by the concat demuxer on all platforms
            f.write(f"file '{str(p).replace(chr(92), '/')}'\n")
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

# Per-platform blur regions as (crop_x, crop_y, width, height).
# Coordinates use ffmpeg expressions: iw/ih = input dimensions.
# Only the corners where that platform actually puts watermarks are blurred.
#
# YouTube:   yt-dlp downloads are clean — no watermark, no blur needed.
# TikTok:    logo in bottom-right; username strip in bottom-left.
# Instagram: yt-dlp downloads are generally clean; skip.
# unknown:   blur all four corners as a safe fallback.
_PLATFORM_BLUR_REGIONS: dict[str, list[tuple]] = {
    "youtube":   [],   # clean download — skip entirely
    "instagram": [],   # clean download — skip entirely
    "tiktok": [
        # bottom-right: TikTok logo (~180×180 px)
        ("iw-180",    "ih-180", 180, 180),
        # bottom-left: username / description strip (~300×100 px)
        ("0",          "ih-100", 300, 100),
    ],
    # "unknown" = any other platform or reposted content.
    # Covers every common watermark position: 4 corners + center-bottom
    # (CapCut, editing apps, news tickers, and misc site watermarks).
    # Detection still runs on each region — only confirmed ones are blurred.
    "unknown": [
        ("0",              "0",       180, 100),   # top-left
        ("iw-180",         "0",       180, 100),   # top-right
        ("0",              "ih-100",  180, 100),   # bottom-left
        ("iw-180",         "ih-100",  180, 100),   # bottom-right
        ("iw/2-200",       "ih-80",   400,  80),   # center-bottom (CapCut etc.)
    ],
}

_BLUR_STRENGTH = 18         # Gaussian sigma — strong enough to be unreadable

# Pixel std-dev threshold: clean background ≈ 0–12, watermark text/logo ≈ 25–70+
_WATERMARK_STDDEV_THRESHOLD   = 22.0
# Mean absolute pixel difference between frames: static watermark ≈ 0–8,
# animated watermark ≈ 8–18, video content ≈ 20–80+
_WATERMARK_TEMPORAL_THRESHOLD = 20.0


def _get_video_size(video_path: Path) -> tuple[int, int]:
    """Return (width, height) of the first video stream."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0", str(video_path),
        ],
        capture_output=True, text=True,
    )
    w, h = result.stdout.strip().split(",")
    return int(w), int(h)


def _resolve_region(
    region: tuple, vw: int, vh: int
) -> tuple[int, int, int, int]:
    """Resolve ffmpeg-expression coords (iw-N, ih-N) to actual pixel values."""
    cx_expr, cy_expr, bw, bh = region
    # Replace iw/ih with actual dimensions, then evaluate the arithmetic
    cx = int(eval(cx_expr.replace("iw", str(vw)).replace("ih", str(vh))))  # noqa: S307
    cy = int(eval(cy_expr.replace("iw", str(vw)).replace("ih", str(vh))))  # noqa: S307
    # Clamp so crop never goes outside the frame
    cx = max(0, min(cx, vw - bw))
    cy = max(0, min(cy, vh - bh))
    return cx, cy, bw, bh


def _get_region_pixels(
    video_path: Path, x: int, y: int, w: int, h: int, seek: float
) -> bytes | None:
    """Extract raw grayscale pixels from a single frame region at `seek` seconds."""
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", str(seek), "-i", str(video_path),
            "-vframes", "1",
            "-vf", f"crop={w}:{h}:{x}:{y}",
            "-f", "rawvideo", "-pix_fmt", "gray",
            "pipe:1",
        ],
        capture_output=True,
    )
    if result.returncode != 0 or len(result.stdout) < w * h // 2:
        return None
    return result.stdout[: w * h]


def _region_has_watermark(
    video_path: Path, x: int, y: int, w: int, h: int
) -> bool:
    """
    Return True if the given region looks like a watermark (text, logo, UI element).

    Two-stage check:
    1. High pixel variance  — something with sharp edges / contrast is here.
    2. Temporal stability   — it barely changes between frames, meaning it's a
       static overlay rather than busy video content that happens to be complex.

    Sampling two frames (at 1 s and 3 s) is fast (~20–40 ms total) and
    reliably separates static watermarks from moving cat footage.
    """
    px1 = _get_region_pixels(video_path, x, y, w, h, seek=1.0)
    if px1 is None:
        return True   # can't read → safe default: assume watermark

    n = len(px1)
    mean1 = sum(px1) / n
    std_dev = (sum((p - mean1) ** 2 for p in px1) / n) ** 0.5

    if std_dev < _WATERMARK_STDDEV_THRESHOLD:
        logger.debug(f"  ({x},{y}): std={std_dev:.1f} → plain background")
        return False

    # High variance detected — now check if it's static (watermark) or moving (content)
    px2 = _get_region_pixels(video_path, x, y, w, h, seek=3.0)
    if px2 is not None and len(px2) == n:
        mean_diff = sum(abs(a - b) for a, b in zip(px1, px2)) / n
        is_watermark = mean_diff < _WATERMARK_TEMPORAL_THRESHOLD
        logger.debug(
            f"  ({x},{y}): std={std_dev:.1f}, Δframes={mean_diff:.1f} → "
            f"{'WATERMARK' if is_watermark else 'video content'}"
        )
        return is_watermark

    # Only got one frame — high variance alone is enough to flag
    logger.debug(f"  ({x},{y}): std={std_dev:.1f} → WATERMARK (single frame)")
    return True


def _blur_source_watermarks(
    input_path: Path,
    output_path: Path,
    platform: str = "unknown",
) -> Path:
    """
    Detect then blur platform watermarks in a source clip.

    1. Look up the candidate regions for this platform.
    2. For each candidate, extract one frame and measure pixel variance.
       Regions with low variance (plain background) are skipped — no blur.
    3. Only regions that actually contain a watermark are blurred.
    4. If nothing is detected the file is copied untouched.
    """
    candidate_regions = _PLATFORM_BLUR_REGIONS.get(
        platform, _PLATFORM_BLUR_REGIONS["unknown"]
    )

    if not candidate_regions:
        shutil.copy2(input_path, output_path)
        return output_path

    # Probe video dimensions once so we can resolve expression-based coords
    try:
        vw, vh = _get_video_size(input_path)
    except Exception:
        vw, vh = 1920, 1080  # safe fallback

    # Check each candidate region — keep only those with actual watermark content
    regions_to_blur = []
    for region in candidate_regions:
        x, y, bw, bh = _resolve_region(region, vw, vh)
        if _region_has_watermark(input_path, x, y, bw, bh):
            regions_to_blur.append(region)
        else:
            logger.debug(f"No watermark detected at ({x},{y}) — skipping blur")

    if not regions_to_blur:
        logger.debug(f"No watermarks detected in {input_path.name} — copying clean")
        shutil.copy2(input_path, output_path)
        return output_path

    logger.debug(
        f"Blurring {len(regions_to_blur)}/{len(candidate_regions)} "
        f"region(s) in {input_path.name}"
    )

    # Build a dynamic split → blur → overlay chain for confirmed regions only
    n = len(regions_to_blur)
    split_labels = "".join(f"[c{i}]" for i in range(n))
    fc_parts = [f"[0:v]split={n + 1}[base]{split_labels}"]

    for i, (cx, cy, bw, bh) in enumerate(regions_to_blur):
        fc_parts.append(
            f"[c{i}]crop={bw}:{bh}:{cx}:{cy},gblur=sigma={_BLUR_STRENGTH}[b{i}]"
        )

    prev = "base"
    for i, (cx, cy, bw, bh) in enumerate(regions_to_blur):
        ox = cx.replace("iw", "W")
        oy = cy.replace("ih", "H")
        nxt = "out" if i == n - 1 else f"o{i}"
        fc_parts.append(f"[{prev}][b{i}]overlay={ox}:{oy}[{nxt}]")
        prev = nxt

    vf = ";".join(fc_parts)

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
        logger.warning(f"Watermark blur failed for {platform} (non-fatal): {e}")
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
    tts_audio: dict | None = None,
) -> Path:
    """
    Build a ranking-style Shorts video from exactly `config.clips_per_video` clips.

    Args:
        clip_paths:     Local video file paths. clip_paths[0] will be ranked #5,
                        clip_paths[-1] will be ranked #1 (randomly ordered by caller).
        title:          Short title shown at top of every clip and on the title card.
        output_path:    Where to write the final MP4.
        config:         Config object (for clip_duration, watermark_text, etc.).
        clip_platforms: Platform name per clip ("youtube", "tiktok", "instagram").
                        If omitted, all clips are treated as "unknown".
    """
    if len(clip_paths) < 2:
        raise ValueError(f"Need at least 2 clips, got {len(clip_paths)}")

    platforms = clip_platforms or ["unknown"] * len(clip_paths)

    def _step(msg: str) -> None:
        logger.debug(msg)
        if on_progress:
            on_progress(msg)

    n = len(clip_paths)
    clip_duration = config.clip_duration

    with tempfile.TemporaryDirectory(prefix="catcentral_") as tmpdir:
        tmp = Path(tmpdir)

        # ── 1. Process each clip (resize + rank overlay) ─────────────────────
        processed: list[Path] = []

        for idx, src in enumerate(clip_paths):
            rank = n - idx  # n=5→rank 5 first, idx=4→rank 1 last
            step1 = tmp / f"step1_rank{rank}.mp4"
            platform = platforms[idx]

            blur_regions = _PLATFORM_BLUR_REGIONS.get(
                platform, _PLATFORM_BLUR_REGIONS["unknown"]
            )
            if blur_regions:
                _step(f"Removing {platform} watermark on clip {idx + 1}/{n}…")
                blurred = tmp / f"blurred_rank{rank}.mp4"
                _blur_source_watermarks(src, blurred, platform)
                _step(f"Processing clip {idx + 1}/{n}  (rank #{rank})…")
                _process_clip(blurred, step1, rank, clip_duration, title)
            else:
                _step(f"Processing clip {idx + 1}/{n}  (rank #{rank})…")
                _process_clip(src, step1, rank, clip_duration, title)

            # Mix TTS voiceover if available for this clip
            rank_tts_list = (tts_audio or {}).get("ranks") or []
            if idx < len(rank_tts_list) and rank_tts_list[idx].exists():
                _step(f"Mixing voice for clip {idx + 1}/{n}  (rank #{rank})…")
                step1_tts = tmp / f"step1_rank{rank}_voiced.mp4"
                _mix_tts_into_clip(step1, rank_tts_list[idx], step1_tts)
                processed.append(step1_tts)
            else:
                processed.append(step1)

        # ── 2. Title card ─────────────────────────────────────────────────────
        _step("Creating title card…")
        title_card = tmp / "title_card.mp4"
        intro_tts = (tts_audio or {}).get("intro")
        _make_title_card(title_card, title, n_clips=n, duration=1.5, tts_audio=intro_tts)

        # ── 3. Concatenate: title card first, then rank 5→1 ──────────────────
        _step("Concatenating all clips…")
        concat_in = [title_card] + processed
        joined = tmp / "joined.mp4"
        _concat_clips(concat_in, joined)

        # ── 4. Add moving @CatCentral watermark ──────────────────────────────
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
                "Install it with:  sudo apt install ffmpeg  (Ubuntu/Debian)\n"
                "                  brew install ffmpeg       (macOS)"
            )
