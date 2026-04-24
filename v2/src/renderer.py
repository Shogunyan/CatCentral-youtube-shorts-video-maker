"""
Renders the final Short: portrait base video + animated text overlays.

Video anatomy (proven viral Shorts format):
  ┌─────────────────────┐
  │  SETUP TEXT (top)   │  ← appears immediately, hooks viewer
  │                     │
  │    [cat video]      │
  │                     │
  │  REACTION TEXT      │  ← pops in at ~60% through, drives comments
  │  (lower third)      │
  │                     │
  │       @CatCentral   │  ← subtle watermark, bottom-right
  └─────────────────────┘

Text is rendered as PNG overlays via Pillow so emoji and special chars
work reliably across all platforms. Falls back to ffmpeg drawtext if
Pillow is not available.
"""

import logging
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Bold fonts available on most Linux/macOS systems
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

VIDEO_W = 1080
VIDEO_H = 1920


def _find_font() -> Optional[str]:
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def _strip_emoji(text: str) -> str:
    """Remove emoji codepoints that ffmpeg drawtext can't render."""
    return "".join(
        c
        for c in text
        if not (
            unicodedata.category(c) in ("So", "Sm")
            or 0x1F000 <= ord(c) <= 0x1FFFF
            or 0x2600 <= ord(c) <= 0x27FF
        )
    ).strip()


def _escape_drawtext(text: str) -> str:
    """Escape text for ffmpeg drawtext filter value."""
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "’")   # smart quote avoids shell escaping issues
    text = text.replace(":", "\\:")
    text = text.replace("%", "\\%")
    return text


class VideoRenderer:
    def __init__(self, config):
        self.config = config
        self.font = _find_font()
        self._pillow_ok = self._check_pillow()

    def render(
        self,
        input_path: Path,
        output_path: Path,
        setup_text: str,
        reaction_text: str,
        watermark_text: str,
        video_duration: float,
    ) -> bool:
        """
        Render final video with three text layers.
        Tries Pillow-based PNG overlays first; falls back to drawtext.
        """
        if self._pillow_ok:
            return self._render_pillow(
                input_path, output_path,
                setup_text, reaction_text, watermark_text, video_duration,
            )
        return self._render_drawtext(
            input_path, output_path,
            setup_text, reaction_text, watermark_text, video_duration,
        )

    # ------------------------------------------------------------------ #
    # Pillow path (preferred)
    # ------------------------------------------------------------------ #

    def _render_pillow(
        self,
        input_path: Path,
        output_path: Path,
        setup_text: str,
        reaction_text: str,
        watermark_text: str,
        video_duration: float,
    ) -> bool:
        from PIL import Image, ImageDraw, ImageFont

        font_path = self.font
        reaction_start = video_duration * 0.55
        tmp_files: List[Path] = []

        try:
            setup_img = self._make_text_image(
                setup_text, font_path, fontsize=72, y_anchor="top",
            )
            reaction_img = self._make_text_image(
                reaction_text, font_path, fontsize=78,
                y_anchor="lower_third", color=(255, 220, 0),
            )
            wm_img = self._make_text_image(
                watermark_text, font_path, fontsize=34,
                y_anchor="bottom_right", alpha=180,
            )

            tmp_setup = Path(tempfile.mktemp(suffix="_setup.png"))
            tmp_react = Path(tempfile.mktemp(suffix="_react.png"))
            tmp_wm = Path(tempfile.mktemp(suffix="_wm.png"))
            setup_img.save(tmp_setup, "PNG")
            reaction_img.save(tmp_react, "PNG")
            wm_img.save(tmp_wm, "PNG")
            tmp_files = [tmp_setup, tmp_react, tmp_wm]

            # Build filter graph:
            # setup fades in over 0.3s from t=0
            # reaction fades in over 0.3s from t=reaction_start
            # watermark stays constant
            filter_complex = (
                f"[0:v][1:v]overlay=0:0:enable='1':alpha='if(lt(t,0.3),t/0.3,1)'[s];"
                f"[s][2:v]overlay=0:0:enable='gte(t,{reaction_start:.2f})'"
                f":alpha='if(lt(t-{reaction_start:.2f},0.3),(t-{reaction_start:.2f})/0.3,1)'[r];"
                f"[r][3:v]overlay=0:0[out]"
            )

            cmd = [
                "ffmpeg", "-y",
                "-i", str(input_path),
                "-i", str(tmp_setup),
                "-i", str(tmp_react),
                "-i", str(tmp_wm),
                "-filter_complex", filter_complex,
                "-map", "[out]",
                "-map", "0:a?",
                "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                "-c:a", "aac", "-ar", "44100", "-b:a", "128k",
                "-movflags", "+faststart",
                str(output_path),
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if result.returncode != 0:
                logger.error(f"Pillow render failed: {result.stderr[-600:]}")
                return False
            return True

        finally:
            for f in tmp_files:
                try:
                    f.unlink(missing_ok=True)
                except Exception:
                    pass

    def _make_text_image(
        self,
        text: str,
        font_path: Optional[str],
        fontsize: int,
        y_anchor: str = "top",
        color: tuple = (255, 255, 255),
        alpha: int = 255,
    ):
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype(font_path, fontsize) if font_path else ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()

        lines = self._wrap_text(draw, text, font, VIDEO_W - 100)
        line_h = fontsize + 10
        block_h = len(lines) * line_h

        if y_anchor == "top":
            y_start = 110
        elif y_anchor == "lower_third":
            y_start = VIDEO_H - 340 - block_h
        elif y_anchor == "bottom_right":
            # Handled per-word below
            y_start = VIDEO_H - 90
        else:
            y_start = (VIDEO_H - block_h) // 2

        if y_anchor == "bottom_right":
            # Single-line watermark aligned right
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            x = VIDEO_W - tw - 30
            y = VIDEO_H - 90
            outline = 2
            for dx in range(-outline, outline + 1):
                for dy in range(-outline, outline + 1):
                    if dx or dy:
                        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 220))
            draw.text((x, y), text, font=font, fill=(*color, alpha))
        else:
            outline = 5
            for i, line in enumerate(lines):
                y = y_start + i * line_h
                bbox = draw.textbbox((0, 0), line, font=font)
                tw = bbox[2] - bbox[0]
                x = (VIDEO_W - tw) // 2
                for dx in range(-outline, outline + 1):
                    for dy in range(-outline, outline + 1):
                        if dx or dy:
                            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 255))
                draw.text((x, y), line, font=font, fill=(*color, alpha))

        return img

    @staticmethod
    def _wrap_text(draw, text: str, font, max_width: int) -> List[str]:
        words = text.split()
        lines: List[str] = []
        current = ""
        for word in words:
            test = f"{current} {word}".strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines or [text]

    # ------------------------------------------------------------------ #
    # drawtext fallback
    # ------------------------------------------------------------------ #

    def _render_drawtext(
        self,
        input_path: Path,
        output_path: Path,
        setup_text: str,
        reaction_text: str,
        watermark_text: str,
        video_duration: float,
    ) -> bool:
        font_arg = f":fontfile={self.font}" if self.font else ""
        reaction_start = video_duration * 0.55

        setup_clean = _escape_drawtext(_strip_emoji(setup_text))
        reaction_clean = _escape_drawtext(_strip_emoji(reaction_text))
        wm_clean = _escape_drawtext(_strip_emoji(watermark_text))

        vf = ",".join([
            (
                f"drawtext=text='{setup_clean}'{font_arg}"
                ":fontsize=68:fontcolor=white:borderw=5:bordercolor=black"
                ":x=(w-text_w)/2:y=120"
                ":alpha='if(lt(t,0.3),t/0.3,1)'"
            ),
            (
                f"drawtext=text='{reaction_clean}'{font_arg}"
                ":fontsize=74:fontcolor=yellow:borderw=5:bordercolor=black"
                f":x=(w-text_w)/2:y=h-320"
                f":enable='gte(t,{reaction_start:.1f})'"
                f":alpha='if(lt(t-{reaction_start:.1f},0.3),(t-{reaction_start:.1f})/0.3,1)'"
            ),
            (
                f"drawtext=text='{wm_clean}'{font_arg}"
                ":fontsize=32:fontcolor=white:borderw=2:bordercolor=black"
                ":alpha=0.7:x=w-text_w-30:y=h-80"
            ),
        ])

        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", vf,
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-c:a", "aac", "-ar", "44100", "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            logger.error(f"drawtext render failed: {result.stderr[-600:]}")
            return False
        return True

    @staticmethod
    def _check_pillow() -> bool:
        try:
            from PIL import Image, ImageDraw, ImageFont  # noqa: F401
            return True
        except ImportError:
            logger.warning("Pillow not installed — falling back to ffmpeg drawtext (no emoji in overlays)")
            return False
