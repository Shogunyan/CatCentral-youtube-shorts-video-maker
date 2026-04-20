"""
caption_gen.py — Generates YouTube titles, descriptions, and tag lists.
"""
import random
import re

# ── Title themes ─────────────────────────────────────────────────────────────

THEMES = [
    {"title": "Top 5 Funniest Cats 😂",              "tt_hashtags": ["funnycat", "top5cats", "funnycats"]},
    {"title": "Cats Ranked 5 to 1 🏆",               "tt_hashtags": ["catranking", "catsranked", "funnycats"]},
    {"title": "Most Viral Cat Moments 🔥",            "tt_hashtags": ["viralcat", "catmoments", "funnycats"]},
    {"title": "5 Cats That Broke The Internet 😱",    "tt_hashtags": ["viralcat", "catinternet", "funnycats"]},
    {"title": "Funniest Cat Clips Ranked 🐱",         "tt_hashtags": ["funnycat", "catclips", "catsranked"]},
    {"title": "Which Cat Is Funniest? 👀",            "tt_hashtags": ["funnycat", "catpoll", "funnycats"]},
    {"title": "Unhinged Cats Ranked 😭",              "tt_hashtags": ["unhingedcat", "chaoticcat", "crazycats"]},
    {"title": "Wild Cat Moments 🐾",                  "tt_hashtags": ["crazycats", "wildcat", "catmoment"]},
    {"title": "Cats Caught Being Chaotic 💀",         "tt_hashtags": ["chaoticcat", "catchaos", "funnycats"]},
    {"title": "Top 5 Cat Reactions 😹",               "tt_hashtags": ["catreaction", "funnycat", "catsoftiktok"]},
    {"title": "Cats Being Weird 😂",                  "tt_hashtags": ["weirdcat", "catsbeingweird", "funnycats"]},
    {"title": "Funniest Cats On The Internet 🌐",     "tt_hashtags": ["funnycat", "internetcat", "funnycats"]},
    {"title": "Cats Are Built Different 😤",          "tt_hashtags": ["catsdifferent", "funnycat", "crazycats"]},
    {"title": "Cats That Went Viral 🔥",              "tt_hashtags": ["viralcat", "famouscat", "funnycats"]},
    {"title": "Top Cat Moments You Need To See 👁️",  "tt_hashtags": ["topcat", "catmoments", "funnycats"]},
]

# ── Description templates ─────────────────────────────────────────────────────

DESCRIPTION_INTROS = [
    "These cats are absolutely unhinged 😂 Ranked from funny to FUNNIEST!",
    "We found the internet's most viral cat clips and ranked them so you don't have to!",
    "Which cat deserves the #1 spot? Drop your vote in the comments 👇",
    "These cats are built different — ranked from wild to WILDEST!",
    "The ultimate cat ranking has arrived. Do you agree with #1? 🏆",
    "Your daily dose of certified unhinged cats, ranked 😹",
    "These clips broke the internet for a reason 😱 Do you agree with the ranking?",
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
# YouTube allows up to 5000 chars in the description. We fill the remaining
# space after the body text with hashtags to maximise discoverability.
# The FIRST THREE hashtags YouTube finds become the video's "topic" tags shown
# under the title — keep the most relevant ones pinned at the front.
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

    body = f"{intro}\n\n{cta}\n{DESCRIPTION_FOOTER}\n\n"

    # Fill remaining description space with hashtags (YouTube limit: 5000 chars).
    # Pinned tags go first (YouTube uses the first 3 as topic tags under the title).
    pool = list(_HASHTAG_POOL)
    if extra_hashtags:
        # Prepend theme-specific hashtags that aren't already pinned
        pinned_lower = {t.lstrip("#").lower() for t in _PINNED_HASHTAGS}
        extras = [
            f"#{ht.lstrip('#')}"
            for ht in extra_hashtags
            if ht.lstrip("#").lower() not in pinned_lower
        ]
        pool = extras + pool
    random.shuffle(pool)
    all_tags = _PINNED_HASHTAGS + pool

    max_len = 4950  # safely under the 5000-char YouTube limit
    remaining = max_len - len(body)
    tag_parts: list[str] = []
    used = 0
    for tag in all_tags:
        sep = 1 if tag_parts else 0   # space between tags
        if used + sep + len(tag) > remaining:
            break
        tag_parts.append(tag)
        used += sep + len(tag)

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
    ("moment", "Cat Moments"),
    ("clip", "Cat Clips"),
    ("reaction", "Cat Reactions"),
    ("fail", "Cat Fails"),
    ("time", "Cat Moments"),
    ("kitten", "Kitten Moments"),
    ("breed", "Cat Breeds"),
    ("sound", "Cat Sounds"),
    ("video", "Cat Moments"),
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
