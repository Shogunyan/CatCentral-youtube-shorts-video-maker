"""
Generates viral-optimised titles, text overlays, and metadata.

Two modes:
  1. Claude AI (if ANTHROPIC_API_KEY is set) — generates contextually relevant,
     unique titles tailored to the actual clip content.
  2. Curated templates — hand-picked based on what consistently performs on
     YouTube Shorts for cat content (POV/relatable format beats "Top 5" every time).

Key insight on viral title formats:
  "Top 5 Funniest Cats" → ~50K avg views
  "POV: your cat at 3am 😭" → 500K+ avg views
  "why is my cat like this 😭" → 1M+ avg views

The relatable/POV format wins because it:
  - Immediately signals personal connection ("your cat")
  - Creates comment intent ("mine does this too!")
  - Works as a loop hook (viewer re-watches to check context)
"""

import random
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────── TITLE TEMPLATES ──────────────────────────── #
# Ordered by expected engagement tier (best first within each group)

TITLE_TEMPLATES = [
    # POV / relatable — highest comment and share rate
    "POV: your cat at 3am 😭",
    "me when my cat does this 💀",
    "why is my cat like this 😭",
    "my cat really said enough 😂",
    "cats are NOT normal animals 💀",
    "my cat has zero shame 😭",
    "cats really are built different 💀",
    "i can't with cats anymore 😭",
    "every cat owner can relate 😂",
    "my cat thinks they own me 💀",
    "living with a cat summed up 😭",
    "the way my cat looked at me 💀",
    # Reaction-lead — drives re-watches
    "this cat really said... 💀😂",
    "bro this cat is actually unhinged 😭",
    "the audacity of this cat 😭",
    "cats are absolutely chaotic 💀",
    "this cat said choose violence 😂",
    "i would NOT survive this cat 💀",
    "i was NOT ready for this 😭",
    "this cat said hold on 😂💀",
    # Question format — boosts comments
    "does your cat do this?? 😂",
    "why do cats do this lmaooo 😭",
    "is this just my cat or... 💀",
    "can someone explain cats to me 😭",
    "which one is your cat? 👇😂",
    # Trending language
    "this cat has more rizz than me 💀",
    "cat said no cap fr fr 😭",
    "cat is literally me fr 😂",
    "this cat said slay and left 💀",
    "cat behaviour is unmatched fr 😭",
    # Surprise/payoff
    "wait for it... 👀💀",
    "the ending got me 💀💀",
    "she really said nope and walked 😭😂",
    "bro really said not today 😂",
    "cats really move different 💀",
]

# ─────────────────────────── OVERLAY TEXTS ────────────────────────────── #

# Setup text shown at TOP of video from t=0 (sets the relatable hook)
SETUP_TEXTS = [
    "my cat at 3am 😭",
    "POV: your cat's morning",
    "cats are not normal 💀",
    "when your cat wakes you up",
    "the chaos is unreal 💀",
    "this cat has no fear 😭",
    "cat said choose violence",
    "cats live rent free here",
    "every cat owner 😭",
    "this is not okay 💀",
    "the audacity 😭💀",
    "cat caught in 4K",
    "totally unhinged 💀",
    "peak cat behaviour 😂",
    "cats are wild fr 💀",
]

# Reaction text shown at ~60% into the video (drives comments + re-watches)
REACTION_TEXTS = [
    "LITERALLY MINE 💀",
    "WHY ARE CATS LIKE THIS 😭",
    "I'M DONE 💀💀",
    "THE AUDACITY 😭",
    "UNHINGED 😭💀",
    "CAT IS BUILT DIFFERENT 💀",
    "NO WAY 😭😂",
    "BAHAHAHA 💀",
    "I CANNOT 😭",
    "ZERO SHAME 💀",
    "ME EVERY DAY 💀😂",
    "THE WAY I SCREAMED 😭",
    "CATS ARE WILD 💀",
    "NOT THEM 💀😂",
    "THEY'RE SO DRAMATIC 😭",
]

# ──────────────────────────── DESCRIPTIONS ────────────────────────────── #

DESCRIPTION_INTROS = [
    "Cats really are something else 😂 Follow for daily cat chaos",
    "This is why cats run the internet 💀 Daily cat content right here",
    "Cat behaviour cannot be explained and that's why we love them 😭",
    "If you have a cat, you KNOW 😂 Subscribe for more",
    "Cats are the unsung heroes of the internet 💀 More daily",
    "The internet exists because of cats and I will not apologise 😭",
    "My cat does this daily and I have zero complaints 💀",
]

DESCRIPTION_CTAS = [
    "Does your cat do this? Drop it below 👇",
    "Tag a cat owner who needs to see this 🐱",
    "Like if your cat is also unhinged 😂",
    "Which part got you? Comment below 👇",
    "Follow for daily cat chaos 🐱💀",
]

# Hashtag pool — first three are algorithm anchors, rest maximise discoverability
HASHTAG_POOL = [
    "#shorts", "#cats", "#funnycat",
    "#catsoftiktok", "#catvideos", "#funnycats", "#catsofinstagram",
    "#kitten", "#kittens", "#catlovers", "#catstagram", "#catlover",
    "#meow", "#catlife", "#catoftheday", "#catmom", "#catdad",
    "#adorablecat", "#cutecat", "#cutecats", "#catbehavior",
    "#catshorts", "#catfunny", "#catmoment", "#catlol", "#catchaos",
    "#catunhinged", "#catbeingacat", "#catsarewild", "#catsbeingweird",
    "#relatable", "#catowner", "#catparent", "#petowner", "#pets",
    "#petsofinstagram", "#petstagram", "#petvideos", "#petlovers",
    "#youtubeshorts", "#shortsvideo", "#viral", "#viralvideo",
    "#trending", "#fyp", "#foryou", "#foryoupage",
    "#funny", "#hilarious", "#comedy", "#lol", "#lmao",
    "#laugh", "#humor", "#memes",
    "#cat", "#catpeople", "#indoorcat", "#housecat", "#tabbycat",
    "#blackcat", "#orangecat", "#siamese", "#mainecoon", "#persiancat",
    "#animals", "#animallovers", "#animalsofinstagram",
    "#purrfect", "#catperson",
]

YOUTUBE_TAGS = [
    "cats", "funny cats", "cat video", "cat funny", "cat shorts",
    "funny cat", "cat moments", "cute cats", "cat behavior",
    "viral cats", "cat fails", "cat reaction", "funny animals",
    "pets", "kitten", "cat meme", "cat tiktok", "cat youtube shorts",
    "daily cats", "cat chaos",
]


class CaptionEngine:
    def __init__(self, anthropic_api_key: str = ""):
        self._client = None
        if anthropic_api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=anthropic_api_key)
                logger.info("Claude AI title generation enabled")
            except ImportError:
                logger.warning("anthropic package not installed — using templates")

    def generate_title(self, context: str = "") -> str:
        """Return a viral-optimised YouTube Short title."""
        if self._client and context:
            try:
                return self._claude_title(context)
            except Exception as e:
                logger.warning(f"Claude title generation failed, using template: {e}")
        return random.choice(TITLE_TEMPLATES)

    def _claude_title(self, context: str) -> str:
        prompt = f"""Generate one viral YouTube Shorts title for a cat video.

Video context: {context}

Rules:
- Max 85 characters
- Include 1-2 emojis (💀 😭 😂 👀 are highest-engagement for cat content)
- Use authentic Gen-Z/internet tone: "fr", "built different", "no cap", "unhinged"
- Sound like a real person posting, not a brand
- Use ONE of these proven formats:
    "POV: your cat [does thing] [emoji]"
    "why is [cat doing thing] [emoji]"
    "[relatable situation] [emoji][emoji]"
    "this cat really said... [emoji]"
    "my cat when [thing] [emoji]"
- Do NOT use: "Top 5", "ranked", "compilation", "countdown", "#"
- Do NOT add quotes around the title

Return ONLY the title text, nothing else."""

        msg = self._client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        title = msg.content[0].text.strip().strip('"').strip("'")
        return title[:97] + "..." if len(title) > 100 else title

    def generate_setup_text(self) -> str:
        return random.choice(SETUP_TEXTS)

    def generate_reaction_text(self) -> str:
        return random.choice(REACTION_TEXTS)

    def generate_description(self, title: str) -> str:
        intro = random.choice(DESCRIPTION_INTROS)
        cta = random.choice(DESCRIPTION_CTAS)
        hashtags = " ".join(HASHTAG_POOL)
        description = f"{intro}\n\n{cta}\n\n{hashtags}"
        return description[:4900]

    def generate_tags(self) -> list:
        return YOUTUBE_TAGS.copy()
