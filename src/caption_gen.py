"""
caption_gen.py — Generates YouTube titles, descriptions, and tag lists.

Each title theme has associated search terms so the scraper can find clips
that actually match what the title says.
"""
import random

# ── Title themes ─────────────────────────────────────────────────────────────
# Each theme: (title_template, [youtube_queries], [tiktok_hashtags])
# The scraper uses these to find clips that match the title.

THEMES = [
    {
        "title": "Funniest Cats",
        "yt_queries": ["funny cat video", "hilarious cat moment", "funny cat caught on camera"],
        "tt_hashtags": ["funnycat", "funnycats", "funnyanimals"],
    },
    {
        "title": "Silliest Cats Ever",
        "yt_queries": ["silly cat video", "goofy cat moment", "cats being silly"],
        "tt_hashtags": ["sillycat", "goofycat", "catsbeingcats"],
    },
    {
        "title": "Hilarious Cat Moments",
        "yt_queries": ["hilarious cat moment", "funny cat clip", "cat being hilarious"],
        "tt_hashtags": ["funnycat", "catmoment", "hilariouscat"],
    },
    {
        "title": "Cats Being Cats",
        "yt_queries": ["cats being cats funny", "cat doing cat things", "cats being weird funny"],
        "tt_hashtags": ["catsbeingcats", "catlife", "funnycats"],
    },
    {
        "title": "Viral Cat Videos",
        "yt_queries": ["viral cat video", "most viral cat moment", "cat video gone viral"],
        "tt_hashtags": ["viralcat", "catsoftiktok", "catvideos"],
    },
    {
        "title": "Wild Cat Moments",
        "yt_queries": ["wild cat moment funny", "crazy cat video", "cats going crazy"],
        "tt_hashtags": ["crazycats", "wildcat", "catmoment"],
    },
    {
        "title": "Unhinged Cats",
        "yt_queries": ["unhinged cat video", "cats being unhinged", "cat losing it funny"],
        "tt_hashtags": ["unhingedcat", "chaoticcat", "crazycats"],
    },
    {
        "title": "Cats Caught Being Chaotic",
        "yt_queries": ["chaotic cat video", "cat causing chaos funny", "cats destroying things"],
        "tt_hashtags": ["chaoticcat", "catchaos", "funnycats"],
    },
    {
        "title": "Weirdest Cat Clips",
        "yt_queries": ["weird cat video funny", "strange cat behavior funny", "cats being weird"],
        "tt_hashtags": ["weirdcat", "catsbeingweird", "funnycats"],
    },
    {
        "title": "Cats Gone Crazy",
        "yt_queries": ["cat going crazy funny", "cat zoomies funny", "cats running wild"],
        "tt_hashtags": ["catzoomies", "crazycat", "funnycats"],
    },
    {
        "title": "Funniest Cat Reactions",
        "yt_queries": ["funny cat reaction video", "cat reacting funny", "cat surprised reaction"],
        "tt_hashtags": ["catreaction", "funnycat", "catsoftiktok"],
    },
    {
        "title": "Cats Doing the Most",
        "yt_queries": ["cat doing the most funny", "extra cat funny", "dramatic cat video"],
        "tt_hashtags": ["dramaticcat", "funnycat", "catdrama"],
    },
]

PERIOD_OPTIONS = ["This Week", "This Month", "All Time", "Right Now", "2025"]  # kept for future use

# ── Description templates ─────────────────────────────────────────────────────

DESCRIPTION_INTROS = [
    "Watch these hilarious cats ranked from funny to FUNNIEST!",
    "We found the internet's best cat clips and ranked them so you don't have to!",
    "Which cat deserves the #1 spot? You decide!",
    "These cats are on another level — ranked from wild to WILDEST!",
    "The ultimate cat ranking has arrived. Do you agree with #1?",
]

DESCRIPTION_CTAs = [
    "Which one was your fav? Comment below!",
    "Do you agree with the ranking? Let us know!",
    "Which clip should be #1? Drop your take!",
    "Tag someone who needs to see this!",
]

DESCRIPTION_FOOTER = """
━━━━━━━━━━━━━━━━━━━━━━━━━━
Subscribe for daily cat content — new videos every day!
Follow us: @CatCentral
━━━━━━━━━━━━━━━━━━━━━━━━━━"""

BASE_TAGS = [
    "cats", "funny cats", "cat videos", "funny animals", "cat memes",
    "cat shorts", "viral cats", "funniest cats", "cat ranking",
    "cat compilation", "cute cats", "hilarious cats", "top 5 cats",
    "cats being cats", "animal videos", "shorts", "youtube shorts",
]


def pick_theme(n: int = 5) -> dict:
    """Pick a random theme and return it with the formatted title."""
    theme = random.choice(THEMES)
    # title is the clean display/YouTube title — no #shorts suffix (added in description/tags)
    title = theme["title"].format(n=n)
    if len(title) > 85:
        title = title[:82] + "..."
    return {
        "title": title,
        "yt_queries": theme["yt_queries"],
        "tt_hashtags": theme["tt_hashtags"],
    }


def generate_description(title: str) -> str:
    intro = random.choice(DESCRIPTION_INTROS)
    cta = random.choice(DESCRIPTION_CTAs)
    return (
        f"{intro}\n\n"
        f"{cta}\n"
        f"{DESCRIPTION_FOOTER}\n\n"
        f"#cats #funnycat #catvideos #shorts #viral #funnyanimal"
    )


def generate_tags(extra: list[str] | None = None) -> list[str]:
    tags = list(BASE_TAGS)
    if extra:
        tags.extend(extra)
    random.shuffle(tags)
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
    Return a dict with title, description, tags, and search terms
    so the scraper can find matching clips.
    """
    theme = pick_theme(n)
    return {
        "title": theme["title"],
        "description": generate_description(theme["title"]),
        "tags": generate_tags(theme["tt_hashtags"]),
        "yt_queries": theme["yt_queries"],
        "tt_hashtags": theme["tt_hashtags"],
    }
