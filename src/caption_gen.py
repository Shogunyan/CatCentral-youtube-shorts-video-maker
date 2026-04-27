"""
caption_gen.py — Generates YouTube titles, descriptions, and tag lists.
"""
import random
import re

# ── Title themes ─────────────────────────────────────────────────────────────

THEMES = [
    {"title": "This Cat Said Absolutely Not 😭",               "tt_hashtags": ["funnycat", "top5cats", "funnycats"]},
    {"title": "The #1 Cat Will Ruin You 💀",                   "tt_hashtags": ["catranking", "catsranked", "funnycats"]},
    {"title": "Nobody Prepared Me For Cat #1 😱",              "tt_hashtags": ["viralcat", "catmoments", "funnycats"]},
    {"title": "These Cats Are A Menace To Society 😤",          "tt_hashtags": ["viralcat", "catinternet", "funnycats"]},
    {"title": "I Can't Stop Watching Cat #1 🔥",               "tt_hashtags": ["funnycat", "catclips", "catsranked"]},
    {"title": "The Audacity Of These Cats 😹",                 "tt_hashtags": ["funnycat", "catpoll", "funnycats"]},
    {"title": "Cat #1 Is An Unhinged Menace 😭",               "tt_hashtags": ["unhingedcat", "chaoticcat", "crazycats"]},
    {"title": "When Cats Forget They're Cats 💀",              "tt_hashtags": ["crazycats", "wildcat", "catmoment"]},
    {"title": "Cats That Understand The Assignment 👑",         "tt_hashtags": ["chaoticcat", "catchaos", "funnycats"]},
    {"title": "The Way Cat #1 Acted Like It Owned Everything 😂", "tt_hashtags": ["catreaction", "funnycat", "catsoftiktok"]},
    {"title": "These Cats Are Operating On A Different Level 😱", "tt_hashtags": ["weirdcat", "catsbeingweird", "funnycats"]},
    {"title": "Cat #1 Did NOT Have To Go That Hard 💀",         "tt_hashtags": ["funnycat", "internetcat", "funnycats"]},
    {"title": "The #1 Cat Lives Rent Free In My Head Now 😂",   "tt_hashtags": ["catsdifferent", "funnycat", "crazycats"]},
    {"title": "These Cats Have No Fear And No Shame 😤",        "tt_hashtags": ["viralcat", "famouscat", "funnycats"]},
    {"title": "This Cat Said 'I Do What I Want' 😹",            "tt_hashtags": ["topcat", "catmoments", "funnycats"]},
]

# ── Description templates ─────────────────────────────────────────────────────

DESCRIPTION_INTROS = [
    "Which cat deserves #1? 99% of people get it wrong 👇",
    "This ranking is going to make you lose it 😭 Watch to the end",
    "The #1 spot will surprise you — do you agree? Drop your ranking below",
    "These cats are not normal. Ranked from wild to UNHINGED 🔥",
    "Warning: do NOT watch this near sleeping people 😂",
    "We ranked the internet's best cats so you don't have to. You're welcome 🏆",
    "Cat owners will relate to every single one of these 😹",
]

DESCRIPTION_CTAs = [
    "Which one was your fav? Comment below! 👇",
    "Do you agree with the ranking? Let us know!",
    "Which clip should be #1? Drop your take 💬",
    "Tag someone who needs to see this 😂",
    "Like if #1 actually got you 😹",
    "Comment your ranking! Do you agree? 👀",
]

DESCRIPTION_FOOTER = """
━━━━━━━━━━━━━━━━━━━━━━━━━━
Subscribe for daily cat content — new videos every day! 🐱
━━━━━━━━━━━━━━━━━━━━━━━━━━"""

BASE_TAGS = [
    "cats", "funny cats", "cat videos", "funny animals", "cat memes",
    "cat shorts", "viral cats", "funniest cats", "cat ranking",
    "cat compilation", "cute cats", "hilarious cats", "top 5 cats",
    "cats being cats", "animal videos", "shorts", "youtube shorts",
]

# ── Hashtag pool for descriptions ─────────────────────────────────────────────
# The FIRST THREE hashtags YouTube finds become the video's "topic" tags shown
# under the title — keep the most relevant ones pinned at the front.
# Total used per video: 15 (3 pinned + 12 from the shuffled pool).
_PINNED_HASHTAGS = ["#shorts", "#cats", "#funnycat"]

_HASHTAG_POOL = [
    # Core discovery
    "#catsoftiktok", "#catvideos", "#funnycats", "#catmemes", "#catmoments",
    "#viralcat", "#catranking", "#funnyanimal", "#catshorts", "#catlover",
    "#catlife", "#kittens", "#kitten", "#kitty", "#meow", "#catlovers",
    "#catvideo", "#catclips", "#funnypets", "#funnypet", "#animalvideos",
    "#catbehavior", "#catfails", "#catfunny", "#catreaction", "#catstagram",
    "#catsbeingcats", "#catworld", "#catdaily", "#catreels", "#cathumor",
    "#funnycatvideos", "#catlol", "#catentertainment", "#catcompilation",
    "#cutecats", "#catcrazy", "#viral", "#trending", "#funny", "#animals",
    "#pets", "#catattack", "#cattok", "#catto", "#cattos", "#kittycat",
    "#tabbycat", "#fluffycat", "#cat", "#kittensofinstagram", "#catloversclub",
    "#catofinstagram", "#fyp", "#foryou", "#catclip", "#funnycatvideo",
    "#catmoment", "#crazycats", "#weirdcat", "#sillycat", "#goofycat",
    "#chaoticcat", "#catfail", "#catchaos", "#catdrama", "#dramaticcat",
    "#catreacts", "#surprisedcat", "#scaredcat", "#catzoomies", "#catbite",
    "#catattacks", "#catslap", "#catjump", "#catfall", "#catknock",
    "#catderp", "#kitten101", "#kittenlove", "#catvideooftheday",
    "#catpage", "#catsofig", "#catsofinstagram", "#catscommunity",
    "#petvideos", "#pethumor", "#animalmemes", "#animalmoments",
    "#animalfails", "#petfails", "#funnyanimals", "#animallover",
    "#petlover", "#shortsvideos", "#youtubeshorts", "#shortsvideo",
    "#instareels", "#reels", "#explore", "#catloaf", "#catface",
    "#floofy", "#catmom", "#catdad", "#catnip", "#purrfect",
    "#meowmeow", "#catperson", "#catobsessed", "#bestcat", "#epiccat",
    "#topcat", "#catranked", "#catclips2024", "#catclips2025",
    "#funnycatclip", "#catmoment2025",
    # Breeds & appearance
    "#persiancat", "#mainecoon", "#siamesecat", "#ragdoll", "#bengalcat",
    "#sphynxcat", "#scottishfold", "#munchkincat", "#abyssinian",
    "#norwegianforestcat", "#birman", "#burmese", "#tonkinese",
    "#russianblue", "#britishcat", "#britishshorthair", "#orangecat",
    "#blackcat", "#whitecat", "#greycat", "#graycat", "#tortoiseshell",
    "#calicocat", "#tuxedocat", "#stripedcat", "#patternedcat",
    "#longhairedcat", "#shorthairedcat", "#floofycat", "#bigcat",
    "#tinykitten", "#babykitten", "#fatcat", "#chonkycat", "#chunkycat",
    # Behaviour & moments
    "#catknocking", "#catsplooting", "#catloafing", "#catpurr",
    "#catpurring", "#catkneading", "#catheadbutt", "#cathiss",
    "#catyell", "#catscream", "#catstare", "#catgaze", "#catblink",
    "#slowblink", "#catsleep", "#catsleeping", "#catnap", "#catnapping",
    "#catrub", "#catgroom", "#catgrooming", "#catplay", "#catplaying",
    "#cathunt", "#catstalk", "#catzap", "#catsprint", "#catpounce",
    "#catchirp", "#catchatter", "#cattrills", "#catyowl", "#catyowling",
    "#catscreaming", "#catwhine", "#catdemand", "#catbeg", "#cathungry",
    "#catatwindow", "#catbirding", "#catsquirrel", "#catoutside",
    "#indoorcat", "#outdoorcat", "#catbalcony", "#catonroof",
    # Relationship & lifestyle
    "#catowner", "#catparent", "#catmomlife", "#catdadlife",
    "#catfamily", "#catsoftheworld", "#catfriends", "#catanddog",
    "#catdog", "#catdoglove", "#catsandkittens", "#twocats",
    "#multiplecats", "#catgang", "#cathouse", "#catapartment",
    "#rescuecat", "#adoptdontshop", "#sheltercat", "#rescuedcat",
    "#catadoption", "#catfoster", "#fostercat", "#seniorcat",
    # Content style tags
    "#animaltiktok", "#animalshorts", "#funnyvideo", "#funnyvideos",
    "#hilarious", "#hilariousvideo", "#lol", "#lmao", "#omg",
    "#mustsee", "#cantmiss", "#watchthis", "#youhavetosee",
    "#cuteness", "#cuteanimals", "#aww", "#awww", "#adorable",
    "#sweet", "#precious", "#wholesome", "#wholesomecontent",
    "#dailycat", "#catsofday", "#catoftheday", "#weeklycat",
    "#catlaughs", "#catcomedian", "#petcomedy", "#animalcomedy",
    "#naturefunny", "#wildlifefunny", "#topcatvideos", "#catbest",
    # Platform & algo boost
    "#fy", "#fypシ", "#fypシ゚viral", "#trending2025", "#viral2025",
    "#viralvideo", "#viralshorts", "#shortsfeed", "#reelsviral",
    "#instagramreels", "#tiktokfunny", "#tiktokanimals", "#tiktokcats",
    "#youtubetrending", "#ytshorts", "#ytshort", "#newvideo",
    "#newcontent", "#dailycontent", "#contentcreator", "#catcontent",
    "#catcontentcreator", "#catsofyoutube", "#youtubecat",
    # Extra cat expressions & slang
    "#catmode", "#catlook", "#catvibes", "#catgang", "#catcrew",
    "#catpack", "#catlife2025", "#catlovers2025", "#catmom2025",
    "#catlady", "#crazycatlady", "#catgentleman", "#catmaniac",
    "#cataddicted", "#catcrazy", "#catenthusiast", "#catsupport",
    "#catcommunity", "#catnetwork", "#catvault", "#catarchive",
    "#catgallery", "#catalbum", "#catcollection", "#cathighlight",
    "#catbest2025", "#catviral2025", "#catshorts2025", "#funnycats2025",
    # More reactions & sounds
    "#catmewl", "#catshriek", "#catsqueak", "#catsigh", "#catgroan",
    "#cathowl", "#catwhimper", "#catbark", "#catgrowl", "#catspat",
    "#cathiss2", "#catrumble", "#catmutter", "#catpant", "#catsnore",
    "#catsmell", "#catstink", "#catsmug", "#catsmile", "#catgrin",
    "#catglare", "#cateye", "#cateyes", "#cattail", "#catpaw",
    "#catpaws", "#catwhisker", "#catwhiskers", "#catear", "#catears",
    "#catnose", "#catmouth", "#catteeth", "#catclaw", "#catclaws",
    "#catfur", "#catcoat", "#catbelly", "#catsoftbelly", "#catfluff",
    # Positions & states
    "#catsit", "#catsitting", "#catstand", "#catstanding", "#catlie",
    "#catlying", "#catstretch", "#catstretching", "#catcurl", "#catcurled",
    "#catwrap", "#catwrapped", "#catball", "#catballed", "#catsploots",
    "#catloaves", "#catmeatloaf", "#catsuperloaf", "#catpretzel",
    "#catupside", "#catflipped", "#catonback", "#cathangdown",
    "#catdangle", "#catdangling", "#catstuck", "#catwedged",
    # Interaction with humans
    "#cathug", "#cathugging", "#catkiss", "#catkissing", "#catcuddle",
    "#catcuddling", "#catsnuggle", "#catsnuggling", "#catpet",
    "#catpetting", "#catbrush", "#catbrushing", "#catbath", "#catbathing",
    "#catnail", "#catnails", "#catvet", "#catvetcheckup", "#catweigh",
    "#catweight", "#catsurprise", "#catprank", "#catscared2",
    "#catcucumber", "#catlemon", "#catzucchini",
    # Quality signals
    "#mustseecat", "#bestcatever", "#ultimatecat", "#legendarycat",
    "#godtiercat", "#elitecatcontent", "#premiumcat", "#toptiercats",
    "#goldencats", "#awardwinningcat", "#oscarcat", "#grammycat",
]


def pick_theme(n: int = 5) -> dict:
    """Pick a random theme and return it with the formatted title."""
    theme = random.choice(THEMES)
    title = theme["title"].format(n=n)
    if len(title) > 85:
        title = title[:82] + "..."
    return {
        "title": title,
        "tt_hashtags": theme["tt_hashtags"],
    }


def generate_description(
    title: str, extra_hashtags: list[str] | None = None
) -> str:
    intro = random.choice(DESCRIPTION_INTROS)
    cta = random.choice(DESCRIPTION_CTAs)

    # Lead with the video title so YouTube indexes it as the description's top keyword.
    clean_title = re.sub(r'\s*#\w+', '', title).strip() if title else ""
    parts = []
    if clean_title:
        parts.append(clean_title)
    parts.append(intro)
    parts.append(f"{cta}\n{DESCRIPTION_FOOTER}")
    body = "\n\n".join(parts) + "\n\n"

    # Fill remaining description space with hashtags (YouTube limit: 5000 chars).
    # Pinned tags go first (YouTube uses the first 3 as topic tags under the title).
    pinned_lower = {t.lstrip("#").lower() for t in _PINNED_HASHTAGS}
    pool = list(_HASHTAG_POOL)
    if extra_hashtags:
        extras = [
            f"#{ht.lstrip('#')}"
            for ht in extra_hashtags
            if ht.lstrip("#").lower() not in pinned_lower
        ]
        # Prepend extras, then deduplicate while preserving order
        seen_tags: set[str] = set(extras)
        deduped_pool = extras + [t for t in pool if t not in seen_tags]
        pool = deduped_pool
    random.shuffle(pool)

    # YouTube uses only the first 3-5 hashtags for ranking; 15 total is the sweet spot.
    # More than ~20 triggers spam heuristics and buries the actual description.
    tag_parts = _PINNED_HASHTAGS + pool[:12]

    return body + " ".join(tag_parts)


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
    """Return a dict with title, description, and tags for a ranking video."""
    theme = pick_theme(n)
    return {
        "title": theme["title"],
        "description": generate_description(
            theme["title"], extra_hashtags=theme["tt_hashtags"]
        ),
        "tags": generate_tags(theme["tt_hashtags"]),
    }


# ── Source-title-aware caption generation ─────────────────────────────────────

_TOP_N_RE = re.compile(r'\btop\s*(\d+)\b', re.IGNORECASE)
_N_TO_1_RE = re.compile(r'\b(\d+)\s*to\s*1\b', re.IGNORECASE)

# (substring-to-detect, clean descriptor we output)
_DESCRIPTORS = [
    ("funniest", "Funniest"),
    ("hilarious", "Funniest"),
    ("funny", "Funniest"),
    ("cutest", "Cutest"),
    ("cute", "Cutest"),
    ("adorable", "Cutest"),
    ("wildest", "Wildest"),
    ("wild", "Wildest"),
    ("chaotic", "Wildest"),
    ("silly", "Funniest"),
    ("goofy", "Funniest"),
    ("craziest", "Craziest"),
    ("crazy", "Craziest"),
    ("best", "Best"),
    ("viral", "Most Viral"),
    ("unhinged", "Most Unhinged"),
    ("random", "Most Random"),
    ("angry", "Angriest"),
    ("dumb", "Dumbest"),
    ("dramatic", "Most Dramatic"),
]

_SUBJECTS = [
    # Appearance / body type — checked first so they win over generic terms
    ("chunky",  "Chunky Cat Moments"),
    ("chonky",  "Chonky Cat Moments"),
    ("chubby",  "Chubby Cat Moments"),
    ("round",   "Round Cat Moments"),
    ("fluffy",  "Fluffy Cat Moments"),
    ("floofy",  "Fluffy Cat Moments"),
    ("giant",   "Giant Cat Moments"),
    ("huge",    "Huge Cat Moments"),
    ("big",     "Big Cat Moments"),
    ("tiny",    "Tiny Cat Moments"),
    ("small",   "Tiny Cat Moments"),
    ("mini",    "Mini Cat Moments"),
    ("fat",     "Fat Cat Moments"),
    ("lazy",    "Lazy Cat Moments"),
    ("sleepy",  "Sleepy Cat Moments"),
    ("grumpy",  "Grumpy Cat Moments"),
    ("derp",    "Derpy Cat Moments"),
    ("angry",   "Angry Cat Moments"),
    ("weird",   "Weird Cat Moments"),
    ("silly",   "Silly Cat Moments"),
    ("goofy",   "Goofy Cat Moments"),
    ("zoomie",  "Cat Zoomie Moments"),
    # Content type — specific first, generic last
    ("kitten",   "Kitten Moments"),
    ("breed",    "Cat Breeds"),
    ("sound",    "Cat Sounds"),
    ("reaction", "Cat Reactions"),
    ("fail",     "Cat Fails"),
    ("clip",     "Cat Clips"),
    ("moment",   "Cat Moments"),
    ("time",     "Cat Moments"),
    ("video",    "Cat Moments"),
]

_TITLE_EMOJIS = ["😂", "😹", "🏆", "🔥", "💀", "😱", "🐱", "😤"]


def generate_caption_from_source(source_title: str, n: int = 5) -> dict:
    """
    Generate a caption whose title closely mirrors the source video's title.
    This keeps the on-screen content and the title consistent (e.g. a
    'Top 5 Funniest Cat Moments' source gets a matching title, not 'Cats
    Are Built Different').
    Falls back to generate_caption() if the source title can't be parsed.
    """
    t = source_title.lower()

    # Extract ranking number
    m = _TOP_N_RE.search(t)
    if m:
        ranking_n = int(m.group(1))
    else:
        m = _N_TO_1_RE.search(t)
        ranking_n = int(m.group(1)) if m else n
    ranking_n = min(max(ranking_n, 3), 10)

    # Detect descriptor
    descriptor = "Funniest"
    for kw, desc in _DESCRIPTORS:
        if kw in t:
            descriptor = desc
            break

    # Detect subject
    subject = "Cat Moments"
    for kw, subj in _SUBJECTS:
        if kw in t:
            subject = subj
            break

    emoji = random.choice(_TITLE_EMOJIS)

    if "ranked" in t or "ranking" in t or "to 1" in t:
        title = f"{subject} Ranked {ranking_n} to 1 {emoji}"
    else:
        title = f"Top {ranking_n} {descriptor} {subject} {emoji}"

    if len(title) > 85:
        title = title[:82] + "..."

    hashtags = ["top5cats", "catranking", "funnycats"]
    if "kitten" in t:
        hashtags = ["kitten", "kittenshorts", "funnycats"]
    elif "cutest" in t or "cute" in t:
        hashtags = ["cutecat", "catmoments", "funnycats"]

    return {
        "title": title,
        "description": generate_description(title, extra_hashtags=hashtags),
        "tags": generate_tags(hashtags),
    }


# ── Channel-copy titles ───────────────────────────────────────────────────────
# Used when copying videos directly from specific channels (no ranking format).
# Picked randomly each run so the channel doesn't look repetitive.

_COPY_TITLES = [
    "You Can't Not Laugh At These Cats 😹",
    "Funniest Cats On The Internet Right Now 🔥",
    "These Cats Are Living Their Best Life 😂",
    "Cats That Forgot They Were Cats 💀",
    "Warning: This Will Make You Want A Cat 🐱",
    "The Internet's Funniest Cat Moments 😱",
    "Cats Being Absolutely Unhinged 😤",
    "Try Not To Laugh — Cat Edition 😂",
    "These Cats Have No Fear 😹",
    "Cats That Broke The Internet This Week 🌐",
    "Nobody Expected The Cat To Do This 💀",
    "Cats Are Built Different And We Love It 🐾",
    "This Is Why Cats Run The Internet 👑",
    "Cats Doing The Most For No Reason 😭",
    "POV: You Came For The Cats And Stayed For The Chaos 🔥",
    "Cats That Woke Up And Chose Violence 😈",
    "Why Are Cats Like This 😂",
    "The Cat Said No And Walked Away 😹",
    "Certified Cat Chaos Compilation 💥",
    "These Cats Are Too Funny To Be Real 😱",
    "Cats Making Their Owners Question Everything 🤣",
    "Main Character Energy — Cat Edition 🐱",
    "Cats That Genuinely Don't Care 😤",
    "Just Cats Being Weird And We're Here For It 🐾",
    "The Cats Are Not Okay And Neither Are We 😂",
    "Cats Who Said 'Watch This' 👀",
    "Every Cat Owner Has Seen This Happen 😹",
    "Cat Behaviour That Can't Be Explained 💀",
    "Cats Having The Time Of Their Lives 🎉",
    "The Funniest Cat Clips You'll See Today 🔥",
]


def generate_copy_caption() -> dict:
    """
    Return a caption for a channel-copy video (non-ranking format).
    Cycles through a pool of pre-written viral-style titles.
    """
    title = random.choice(_COPY_TITLES)
    hashtags = ["funnycats", "catvideos", "catsoftiktok"]
    return {
        "title": title,
        "description": generate_description(title, extra_hashtags=hashtags),
        "tags": generate_tags(hashtags),
    }


# ── WebVTT captions & chapter timestamps ─────────────────────────────────────

def _fmt_vtt(ms: float) -> str:
    """Format milliseconds as HH:MM:SS.mmm for WebVTT."""
    ms = max(0.0, ms)
    h = int(ms // 3_600_000); ms -= h * 3_600_000
    m = int(ms // 60_000);    ms -= m * 60_000
    s = int(ms // 1_000);     frac = int(ms - s * 1_000)
    return f"{h:02d}:{m:02d}:{s:02d}.{frac:03d}"


def generate_srt(
    title: str,
    duration: float,
    n_clips: int = 5,
    is_ranking: bool = True,
) -> str:
    """
    Return WebVTT caption content for a cat ranking (or copy) video.
    Ranking videos get one cue per clip counting down from n_clips to 1.
    Copy videos get a single cue covering the full duration.
    """
    lines = ["WEBVTT", ""]
    if is_ranking and n_clips > 0 and duration > 0:
        seg = duration / n_clips
        for i in range(n_clips):
            start_ms = i * seg * 1000
            end_ms   = (i + 1) * seg * 1000
            rank = n_clips - i
            lines += [f"{_fmt_vtt(start_ms)} --> {_fmt_vtt(end_ms)}", f"Cat #{rank}", ""]
    elif duration > 0:
        clean = re.sub(r'\s*#\w+', '', title).strip() if title else "Cat video"
        lines += [f"00:00:00.000 --> {_fmt_vtt(duration * 1000)}", clean or "Cat video", ""]
    return "\n".join(lines)


def generate_chapter_timestamps(duration: float, n_clips: int = 5) -> str:
    """
    Return YouTube chapter timestamp lines for a ranking video description.
    Format: '0:00 Cat #5\\n0:06 Cat #4\\n...' — YouTube requires the first
    chapter at 0:00 and at least 3 chapters total for chapters to activate.
    """
    if duration <= 0 or n_clips <= 0:
        return ""
    seg = duration / n_clips
    lines = []
    for i in range(n_clips):
        t    = i * seg
        rank = n_clips - i
        mm   = int(t // 60)
        ss   = int(t % 60)
        lines.append(f"{mm}:{ss:02d} Cat #{rank}")
    return "\n".join(lines)
