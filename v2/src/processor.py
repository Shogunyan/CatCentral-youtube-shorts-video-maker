"""
Downloads and processes a source clip into a clean 1080x1920 portrait base video.

Steps:
1. Download via yt-dlp (best quality ≤1080p)
2. Detect the peak-action moment using audio loudness analysis
3. Trim to target duration centred around that peak
4. Scale/crop to 1080×1920 (YouTube Shorts portrait)
"""

import subprocess
import json
import re
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ClipProcessor:
    def __init__(self, config):
        self.config = config

    def download(self, url: str, output_dir: Path) -> Optional[Path]:
        """Download video, return local path or None on failure."""
        output_dir.mkdir(parents=True, exist_ok=True)
        template = str(output_dir / "%(id)s.%(ext)s")

        cmd = [
            "yt-dlp",
            url,
            "-o", template,
            "--format",
            (
                "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]"
                "/bestvideo[height<=1080]+bestaudio"
                "/best[height<=1080]/best"
            ),
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--no-warnings",
            "--quiet",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error(f"Download failed ({url}): {result.stderr[-300:]}")
            return None

        video_id = self._extract_video_id(url)
        if video_id:
            for f in output_dir.glob(f"{video_id}.*"):
                if f.suffix.lower() in (".mp4", ".webm", ".mkv"):
                    return f

        # Fallback: most recently modified mp4
        mp4s = sorted(output_dir.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
        return mp4s[0] if mp4s else None

    def get_duration(self, path: Path) -> float:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            str(path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return 0.0
        try:
            return float(json.loads(result.stdout)["format"]["duration"])
        except (KeyError, ValueError, json.JSONDecodeError):
            return 0.0

    def find_peak_moment(self, path: Path) -> float:
        """
        Return the start offset (seconds) for the best clip window.

        Uses ffmpeg astats to scan RMS loudness per frame and finds the
        loudest sustained burst, which strongly correlates with the funniest
        or most surprising moment in cat content.
        """
        duration = self.get_duration(path)
        if duration <= 0:
            return 0.0

        cmd = [
            "ffmpeg", "-i", str(path),
            "-af",
            "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
            "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        peaks = []
        current_time = 0.0
        for line in result.stderr.split("\n"):
            if "pts_time:" in line:
                try:
                    current_time = float(line.split("pts_time:")[1].strip())
                except ValueError:
                    pass
            elif "lavfi.astats.Overall.RMS_level=" in line:
                try:
                    level = float(line.split("=")[1])
                    if level > -50:
                        peaks.append((current_time, level))
                except ValueError:
                    pass

        if not peaks:
            return max(0.0, duration * 0.25)

        peak_time = max(peaks, key=lambda x: x[1])[0]
        target_dur = min(self.config.target_duration, duration)
        # Centre clip around peak but bias toward peak being 40% in (hook first)
        start = max(0.0, peak_time - target_dur * 0.4)
        start = min(start, max(0.0, duration - target_dur))
        return start

    def trim_and_scale(
        self, input_path: Path, output_path: Path, start_time: float, duration: float
    ) -> bool:
        """
        Trim to [start_time, start_time+duration] and scale/crop to 1080x1920.

        Scale strategy: scale up so the shorter dimension fills the frame,
        then centre-crop. This preserves the subject (usually the cat) in
        the middle of the frame rather than letterboxing.
        """
        vf = (
            "[0:v]"
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            "fps=30"
            "[v]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_time),
            "-i", str(input_path),
            "-t", str(duration),
            "-filter_complex", vf,
            "-map", "[v]",
            "-map", "0:a?",
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-c:a", "aac", "-ar", "44100", "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error(f"Trim/scale failed: {result.stderr[-500:]}")
            return False
        return True

    @staticmethod
    def _extract_video_id(url: str) -> Optional[str]:
        for pattern in (r"youtu\.be/([a-zA-Z0-9_-]{11})", r"[?&]v=([a-zA-Z0-9_-]{11})"):
            m = re.search(pattern, url)
            if m:
                return m.group(1)
        return None
