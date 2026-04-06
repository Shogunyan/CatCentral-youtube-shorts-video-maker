"""
caption_gen.py — Generates YouTube titles, descriptions, and tag lists.

All generation is template-based (no LLM required).  Templates are shuffled
so repeated uploads don't look identical.
"""
import random
from datetime import datetime

# ── Title templates ───────────────────────────────────────────────────────────

TITLE_TEMPLATES = [
    "Top {n} Funniest Cat Videos 😂 #shorts",
    "Ranking {n} HILARIOUS Cat Moments 🐱 #shorts",
    "These {n} Cats Broke The Internet 💀 #shorts",
    "{n} Cat Videos You CANNOT Stop Watching 😹 #shorts",
    "Ranking The Funniest Cats On The Internet #{n} to #1 #shorts",
    "Top {n} Cat Videos That Will Make You Cry Laughing 😂 #shorts",
    "{n} Times Cats Were Absolute GOOFBALLS 🐾 #shorts",
    "The {n} Best Cat Videos Ranked 🏆 #shorts",
    "Top {n} Viral Cat Moments of {period} 🐱 #shorts",
    "{n} Cat Clips Ranked Funniest to FUNNIEST 😹 #shorts",
    "Cats Being Cats — Top {n} Moments Ranked 🐈 #shorts",
    "Can You Guess #1? Top {n} Cat Videos 🎯 #shorts",
    "Top {n} Cats Living Their Best Life 😂 #shorts",
    "{n} Cats That Had NO Idea What They Were Doing 💀 #shorts",
    "Ranking {n} of The Most CHAOTIC Cat Videos 🌀 #shorts",
]

PERIOD_OPTIONS = ["This Week", "This Month", "All Time", "Right Now", "2025"]

# ── Description templates ─────────────────────────────────────────────────────

DESCRIPTION_INTROS = [
    "🐱 Watch these hilarious cats ranked from funny to FUNNIEST!",
    "😂 We found the internet's best cat clips and ranked them so you don't have to!",
    "🐾 Which cat deserves the #1 spot? You decide!",
    "😹 These cats are on another level — ranked from wild to WILDEST!",
    "🏆 The ultimate cat ranking has arrived. Do you agree with #1?",
    "🐱 Five chaotic cats, one ultimate ranking. Who takes the crown?",
]

DESCRIPTION_CTAs = [
    "Which one was your fav? Comment below! 👇",
    "Do you agree with the ranking? Let us know! 💬",
    "Which clip should be #1? Drop your take! ⬇️",
    "Tag someone who needs to see this! 🏷️",
    "Save this for a bad day — guaranteed smile! 😊",
]

DESCRIPTION_FOOTER = """
━━━━━━━━━━━━━━━━━━━━━━━━━━
🔔 Subscribe for daily cat content — new videos every day!
📲 Follow us: @CatCentral
━━━━━━━━━━━━━━━━━━━━━━━━━━"""

HASHTAG_POOLS = [
    # Pool A — general cat
    "#cats #funnycat #catvideos #catlover #catlife #meow #kitty #kittens",
    # Pool B — viral / trending
    "#viral #trending #funny #funnyanimal #animalsoftiktok #petsoftiktok",
    # Pool C — Shorts specific
    "#shorts #youtubeshortsvideos #ytshorts #shortsviral #shortsfunny",
    # Pool D — niche cat
    "#catsoftiktok #catmemes #catbehavior #crazycats #sillycat #fluffycat",
    # Pool E — engagement
    "#lol #hilarious #cutepets #aww #mood #relatable",
]

BASE_TAGS = [
    "cats",
    "funny cats",
    "cat videos",
    "funny animals",
    "cat memes",
    "cat shorts",
    "viral cats",
    "funniest cats",
    "cat ranking",
    "cat compilation",
    "cute cats",
    "hilarious cats",
    "top 5 cats",
    "cats being cats",
    "animal videos",
    "pet videos",
    "shorts",
    "youtube shorts",
]


def generate_title(n: int = 5) -> str:
    template = random.choice(TITLE_TEMPLATES)
    period = random.choice(PERIOD_OPTIONS)
    return template.format(n=n, period=period)


def generate_description(title: str) -> str:
    intro = random.choice(DESCRIPTION_INTROS)
    cta = random.choice(DESCRIPTION_CTAs)

    # Pick 2 random hashtag pools
    pools = random.sample(HASHTAG_POOLS, 2)
    hashtags = "  ".join(pools)

    return (
        f"{intro}\n\n"
        f"{cta}\n"
        f"{DESCRIPTION_FOOTER}\n\n"
        f"{hashtags}"
    )


def generate_tags(extra: list[str] | None = None) -> list[str]:
    tags = list(BASE_TAGS)
    if extra:
        tags.extend(extra)
    random.shuffle(tags)
    # YouTube allows up to 500 chars total in tags
    result = []
    total = 0
    for t in tags:
        if total + len(t) + 1 > 490:
            break
        result.append(t)
        total += len(t) + 1
    return result


def generate_caption(n: int = 5) -> dict:
    """
    Return a dict with title, description, and tags ready for YouTube upload.
    """
    title = generate_title(n)
    return {
        "title": title,
        "description": generate_description(title),
        "tags": generate_tags(),
    }
