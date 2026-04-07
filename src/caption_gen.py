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
        "title": "Top {n} Funniest Cat Memes",
        "yt_queries": ["funny cat meme shorts", "cat meme compilation shorts", "funniest cat memes shorts"],
        "tt_hashtags": ["funnycat", "catmemes", "funnycatmemes"],
    },
    {
        "title": "Top {n} Cats Being ABSOLUTE Goofballs",
        "yt_queries": ["cats being goofy shorts", "goofy cats funny shorts", "silly cats shorts"],
        "tt_hashtags": ["sillycat", "goofycat", "catsbeingcats"],
    },
    {
        "title": "{n} Cat Fails That Had Me DYING",
        "yt_queries": ["cat fail funny shorts", "hilarious cat fails shorts", "cats failing compilation shorts"],
        "tt_hashtags": ["catfail", "catfails", "funnycatfails"],
    },
    {
        "title": "Top {n} Cats vs Cucumbers",
        "yt_queries": ["cats vs cucumbers shorts", "cat scared of cucumber shorts", "cat cucumber reaction shorts"],
        "tt_hashtags": ["catvscucumber", "catcucumber", "catscared"],
    },
    {
        "title": "{n} Cats That Chose VIOLENCE",
        "yt_queries": ["angry cat funny shorts", "cat attack funny shorts", "cats choosing violence shorts"],
        "tt_hashtags": ["angrycat", "catattack", "meancat"],
    },
    {
        "title": "Ranking {n} CHAOTIC Cat Moments",
        "yt_queries": ["chaotic cat moments shorts", "crazy cat moments shorts", "cats being chaotic shorts"],
        "tt_hashtags": ["crazycats", "chaoticcat", "catmoment"],
    },
    {
        "title": "Top {n} Cat Jumps Gone WRONG",
        "yt_queries": ["cat jump fail shorts", "cat jumping fails shorts", "cats jumping funny shorts"],
        "tt_hashtags": ["catjump", "catjumpfail", "funnycats"],
    },
    {
        "title": "{n} Cats With ZERO Brain Cells",
        "yt_queries": ["dumb cat funny shorts", "stupid cats funny shorts", "cats being dumb shorts"],
        "tt_hashtags": ["dumbcat", "stupidcat", "catsbraincell"],
    },
    {
        "title": "Top {n} Cat vs Dog Moments",
        "yt_queries": ["cat vs dog funny shorts", "cats fighting dogs shorts", "cat and dog funny shorts"],
        "tt_hashtags": ["catvsdog", "catanddog", "funnyanimals"],
    },
    {
        "title": "Top {n} Startled Cat Reactions",
        "yt_queries": ["cat startled funny shorts", "cats getting scared shorts", "cat surprised reaction shorts"],
        "tt_hashtags": ["startledcat", "scaredcat", "catreaction"],
    },
    {
        "title": "{n} Cats Knocking Things Off Tables",
        "yt_queries": ["cat knocking things off table shorts", "cats pushing things off shorts", "cat table fail shorts"],
        "tt_hashtags": ["catknockingthingsoff", "catsbeingjerks", "catpush"],
    },
    {
        "title": "Top {n} Viral Cat Clips of {period}",
        "yt_queries": ["viral cat videos shorts", "most viral cat clips shorts", "trending cat videos shorts"],
        "tt_hashtags": ["viralcat", "catsoftiktok", "catvideos"],
    },
    {
        "title": "Top {n} Cats Living Their Best Life",
        "yt_queries": ["cats living best life shorts", "spoiled cat funny shorts", "happy cat moments shorts"],
        "tt_hashtags": ["catlife", "happycat", "catlover"],
    },
    {
        "title": "{n} Cats That Had NO Idea What Was Coming",
        "yt_queries": ["cat surprised shorts", "unexpected cat moments shorts", "cat plot twist shorts"],
        "tt_hashtags": ["catsurprise", "catmoment", "funnycatvideo"],
    },
]

PERIOD_OPTIONS = ["This Week", "This Month", "All Time", "Right Now", "2025"]

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
    period = random.choice(PERIOD_OPTIONS)
    title = theme["title"].format(n=n, period=period)
    # Strip emoji from title if it's too long
    if len(title) > 85:
        title = title[:82] + "..."
    return {
        "title": title + " #shorts",
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
