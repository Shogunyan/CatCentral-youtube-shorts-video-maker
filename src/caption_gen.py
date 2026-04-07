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
    # title is the clean display/YouTube title — no #shorts suffix (added in description/tags)
    title = theme["title"].format(n=n)
    if len(title) > 85:
        title = title[:82] + "..."
    return {
        "title": title,
        "yt_queries": theme["yt_queries"],
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
    """
    Return a dict with title, description, tags, and search terms
    so the scraper can find matching clips.
    """
    theme = pick_theme(n)
    return {
        "title": theme["title"],
        "description": generate_description(
            theme["title"], extra_hashtags=theme["tt_hashtags"]
        ),
        "tags": generate_tags(theme["tt_hashtags"]),
        "yt_queries": theme["yt_queries"],
        "tt_hashtags": theme["tt_hashtags"],
    }
