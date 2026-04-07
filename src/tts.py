"""
tts.py — AI voiceover generation using Microsoft Edge TTS (free, no API key).

Default voice: en-US-GuyNeural — the male "Reddit narrator" voice used by
countless viral compilation and ranking channels.

Other good voices (set TTS_VOICE in .env):
  en-US-EricNeural          — similar male style
  en-US-ChristopherNeural   — slightly deeper male
  en-GB-RyanNeural          — British male narrator
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "en-US-GuyNeural"

# ── Script templates ──────────────────────────────────────────────────────────

INTRO_SCRIPTS = [
    "These are the {n} funniest cat videos on the internet — ranked!",
    "We found the {n} most hilarious cat clips out there. Let's go!",
    "Get ready — here are the {n} funniest cats on the internet!",
    "Warning: these {n} cat videos may cause uncontrollable laughter.",
    "These cats are absolutely unhinged. Here are the top {n}!",
    "You are NOT ready for these {n} cat videos. Let's rank them!",
    "The funniest cats on the internet — ranked. Number one will break you.",
    "These {n} cats went completely viral — and you need to see why.",
    "Cat chaos incoming. Here are {n} clips you can't miss!",
    "Hold on — these {n} cats are on a completely different level.",
]

RANK_LINES: dict[int, list[str]] = {
    5: [
        "Coming in at number five...",
        "Kicking things off at number five...",
        "Starting strong — number five!",
        "First up, at number five...",
        "Opening with number five...",
    ],
    4: [
        "At number four...",
        "Coming in at number four...",
        "Up next at number four...",
        "Moving on — number four!",
        "Number four, and it's a good one...",
    ],
    3: [
        "Number three...",
        "Right in the middle — number three...",
        "Sitting at number three...",
        "At number three...",
        "Halfway through — number three!",
    ],
    2: [
        "So close — number two...",
        "The runner up — number two...",
        "At number two...",
        "Number two! Almost there...",
        "Just missing the top spot — number two...",
    ],
    1: [
        "And the number one funniest cat video is...",
        "Taking the top spot — number one!",
        "The undisputed winner — number one!",
        "And finally... the number one pick!",
        "The one you've been waiting for — number one!",
    ],
}

# Fallback for ranks outside 1-5 (in case clips_per_video is different)
def _rank_line(rank: int) -> str:
    if rank in RANK_LINES:
        return random.choice(RANK_LINES[rank])
    return f"Number {rank}!"

OUTRO_SCRIPTS = [
    "Which one was your favorite? Comment below — and subscribe for more daily cat content!",
    "Did we get the ranking right? Let us know! Hit subscribe for more cat videos every day.",
    "Drop a comment with your favorite! And subscribe — we post cat content every single day.",
    "Was number one really the funniest? Comment your pick and subscribe for more!",
]


# ── Audio helpers ─────────────────────────────────────────────────────────────

def get_audio_duration(path: Path) -> float:
    """Return duration of an audio file in seconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        info = json.loads(result.stdout)
        return float(info["format"]["duration"])
    except Exception:
        return 2.0  # fallback


def has_audio_stream(path: Path) -> bool:
    """Return True if the video/audio file contains an audio stream."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() == "audio"


# ── TTS generator ─────────────────────────────────────────────────────────────

class TTSGenerator:
    """
    Generates MP3 voiceover files using Microsoft Edge TTS (edge-tts package).
    Files are written to `out_dir` and remain until the caller deletes them.
    """

    def __init__(self, voice: str = DEFAULT_VOICE):
        self.voice = voice

    # ── Internal async generation ─────────────────────────────────────────────

    async def _speak(self, text: str, output_path: Path) -> None:
        import edge_tts  # imported lazily so missing package gives a clear error
        communicate = edge_tts.Communicate(text, self.voice)
        await communicate.save(str(output_path))

    def _generate(self, text: str, output_path: Path) -> Path:
        """Synchronous wrapper — safe to call from any thread."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._speak(text, output_path))
        finally:
            loop.close()

        if not output_path.exists() or output_path.stat().st_size < 512:
            raise RuntimeError(
                f"edge-tts produced no output for text: {text!r}\n"
                "Make sure edge-tts is installed:  pip install edge-tts"
            )
        return output_path

    # ── Public API ────────────────────────────────────────────────────────────

    def generate_intro(self, n: int, out_dir: Path) -> Path:
        text = random.choice(INTRO_SCRIPTS).format(n=n)
        logger.debug(f"TTS intro: {text!r}")
        return self._generate(text, out_dir / "tts_intro.mp3")

    def generate_rank(self, rank: int, out_dir: Path) -> Path:
        text = _rank_line(rank)
        logger.debug(f"TTS rank {rank}: {text!r}")
        return self._generate(text, out_dir / f"tts_rank{rank}.mp3")

    def generate_outro(self, out_dir: Path) -> Path:
        text = random.choice(OUTRO_SCRIPTS)
        logger.debug(f"TTS outro: {text!r}")
        return self._generate(text, out_dir / "tts_outro.mp3")

    def generate_all(self, n: int, out_dir: Path) -> dict:
        """
        Generate every TTS clip needed for a ranking video of `n` clips.

        Returns:
            {
              "intro": Path,           # plays over title card
              "ranks": [Path, ...],    # index 0 = rank n (shown first), index n-1 = rank 1
              "outro": Path,           # plays at end
            }
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Generating TTS voiceover  ({self.voice})…")

        intro = self.generate_intro(n, out_dir)
        logger.info(f"  ✓ intro  ({get_audio_duration(intro):.1f}s)")

        ranks: list[Path] = []
        for rank in range(n, 0, -1):   # n → 1
            p = self.generate_rank(rank, out_dir)
            ranks.append(p)
            logger.info(f"  ✓ rank #{rank}  ({get_audio_duration(p):.1f}s)")

        outro = self.generate_outro(out_dir)
        logger.info(f"  ✓ outro  ({get_audio_duration(outro):.1f}s)")

        return {"intro": intro, "ranks": ranks, "outro": outro}
