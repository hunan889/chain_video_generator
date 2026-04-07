"""POSE_SYNONYMS dictionary + query expansion helpers.

Ported from ``api/services/pose_synonyms.py`` (the old monolith) for use
inside the gateway. The previous gateway port lost this entire layer
silently — see commit history for the regression that caused
'oral sex' → reverse_cowgirl.

Pure Python, no dependencies. Safe to import in any context.
"""

from __future__ import annotations

# Pose synonym mappings: pose_key -> list of natural-language phrases
# (English + Chinese) that should be considered as referring to that pose.
POSE_SYNONYMS: dict[str, list[str]] = {
    # === Classic positions ===
    "cowgirl": [
        "woman on top",
        "girl on top",
        "riding",
        "woman riding",
        "riding position",
        "she rides him",
        "she rides on top",
        "woman dominant",
        "female superior",
        "女上位",
    ],
    "reverse_cowgirl": [
        "reverse cowgirl",
        "reverse woman on top",
        "reverse riding",
        "backwards riding",
        "woman backwards",
        "backwards on top",
        "reverse girl on top",
        "riding backwards",
        "from backwards",
        "backwards cowgirl",
        "she faces away while riding",
        "反向女上位",
        "反骑",
    ],
    "missionary": [
        "man on top",
        "face to face",
        "classic position",
        "lying on bed",
        "lying down",
        "spread legs",
        "legs spread",
        "on her back",
        "missionary position",
        "vanilla sex",
        "传教士",
        "正常位",
    ],
    "doggy": [
        "from behind",
        "rear entry",
        "on all fours",
        "on her fours",
        "hands and knees",
        "doggy style",
        "doggystyle",
        "pounded from behind",
        "bent over from behind",
        "后入",
        "后入式",
    ],
    "standing": [
        "standing sex",
        "standing position",
        "vertical",
        "站立式",
    ],
    "spooning": [
        "side by side",
        "lying side",
        "侧卧式",
    ],
    "lotus": [
        "lotus position",
        "sitting face to face",
        "wrapped legs",
        "sitting embrace",
        "sitting on lap",
        "lap sitting",
        "face to face sitting",
        "莲花坐",
        "坐莲",
        "面对面坐",
    ],
    "against_wall": [
        "wall sex",
        "wall fuck",
        "pushed against wall",
        "pinned against wall",
        "up against wall",
        "standing wall",
        "wall standing",
        "pressed to wall",
        "against the wall",
        "壁式",
        "靠墙",
        "墙壁式",
    ],
    "face_down_ass_up": [
        "face down",
        "ass up",
        "prone position",
        "face in pillow",
        "lying face down",
        "趴跪式",
        "趴着",
        "脸朝下屁股朝上",
    ],
    # === Oral ===
    "blowjob": [
        "oral sex",
        "fellatio",
        "oral",
        "bj",
        "sucking cock",
        "sucking penis",
        "sucking dick",
        "sucks his dick",
        "sucks his cock",
        "sucks cock",
        "sucks dick",
        "mouth on cock",
        "give head",
        "giving head",
        "going down on him",
        "she goes down on him",
        "mouth fuck",
        "oral pleasure",
        "口交",
    ],
    "deepthroat": [
        "deep throat",
        "throat fuck",
        "throating",
        "throat penetration",
        "gagging",
        "gagging on cock",
        "gag on cock",
        "balls deep mouth",
        "深喉",
        "喉交",
    ],
    "double_blowjob": [
        "double bj",
        "two girls blowjob",
        "two women sucking",
        "dual blowjob",
        "shared blowjob",
        "twin blowjob",
        "two mouths",
        "双人口交",
        "双飞口交",
    ],
    "69": [
        "sixty nine",
        "69 position",
        "mutual oral",
        "69式",
    ],
    # === Hand / foot ===
    "handjob": [
        "hand job",
        "stroking cock",
        "stroking penis",
        "stroking his cock",
        "jacking off",
        "jerking off",
        "manual stimulation",
        "手交",
        "撸管",
    ],
    "footjob": [
        "foot job",
        "feet job",
        "using feet",
        "using foot",
        "using her feet",
        "using his feet",
        "with her feet",
        "with his feet",
        "with her foot",
        "with his foot",
        "feet on penis",
        "foot on penis",
        "foot on cock",
        "toes on cock",
        "feet stimulation",
        "foot fetish",
        "foot sex",
        "sex with feet",
        "sex with her feet",
        "feet play",
        "she uses her feet on his cock",
        "足交",
    ],
    "fingering": [
        "finger pussy",
        "finger vagina",
        "fingers inside",
        "finger fuck",
        "finger insertion",
        "manual penetration",
        "指交",
        "手指插入",
    ],
    # === Breasts ===
    "paizuri": [
        "titjob",
        "tit job",
        "breast sex",
        "tit fuck",
        "titfuck",
        "between breasts",
        "between her breasts",
        "boob job",
        "boobjob",
        "乳交",
    ],
    "breast_play": [
        "breast fondling",
        "groping breasts",
        "squeezing breasts",
        "breast massage",
        "tit groping",
        "breast grab",
        "breast worship",
        "breast sucking",
        "揉胸",
        "摸奶",
        "胸部按摩",
    ],
    # === Masturbation ===
    "vaginal_masturbation": [
        "clit",
        "clitoris",
        "finger pussy",
        "fingering vagina",
        "pussy fingering",
        "vibrator",
        "dildo",
        "阴道自慰",
        "阴蒂",
        "插阴道",
        "震动棒",
    ],
    "breast_masturbation": [
        "nipple",
        "nipples",
        "pinch nipples",
        "touch breasts",
        "breast play",
        "乳房自慰",
        "乳头",
        "玩乳头",
        "摸胸",
    ],
    # === Cumshot / facial ===
    "facial": [
        "facial cumshot",
        "cum on face",
        "cum on her face",
        "cum face",
        "cumshot face",
        "jizz on face",
        "load on face",
        "face cum",
        "cum facial",
        "shoot on face",
        "ejaculate on face",
        "颜射",
        "射脸",
        "射在脸上",
    ],
    "bukkake": [
        "multiple facial",
        "group facial",
        "cum shower",
        "multiple cumshots",
        "cum bath",
        "mass facial",
        "group cum",
        "multiple men cum",
        "颜射群交",
        "多人颜射",
    ],
    # === Anal ===
    "anal": [
        "anal sex",
        "anal fuck",
        "ass fuck",
        "ass fucking",
        "butt sex",
        "butt fucking",
        "anal penetration",
        "penis in anus",
        "backdoor",
        "anus",
        "ass play",
        "肛交",
        "后庭",
    ],
    # === Group ===
    "threesome": [
        "three way",
        "threeway",
        "three people",
        "mmf",
        "ffm",
        "three some",
        "triple",
        "三人行",
        "3P",
    ],
    "gangbang": [
        "gang bang",
        "group sex",
        "multiple men",
        "multiple partners",
        "group fuck",
        "multiple men fucking her",
        "群交",
        "轮奸",
    ],
    # === Special ===
    "bondage": [
        "tied up",
        "tied to bed",
        "restraints",
        "bdsm",
        "bound",
        "rope bondage",
        "tied with rope",
        "handcuffs",
        "bound wrists",
        "blindfolded",
        "束缚",
        "捆绑",
        "绑缚",
    ],
    "strap_on": [
        "strapon",
        "strap on dildo",
        "strap on",
        "pegging",
        "pegging him",
        "wear dildo",
        "harness dildo",
        "穿戴式",
        "假阳具",
        "佩戴式",
    ],
}


def get_synonyms(pose_key: str) -> list[str]:
    """Return the synonym list for a pose_key (empty list if unknown)."""
    return POSE_SYNONYMS.get(pose_key, [])


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
#
# Match strategy: literal phrase containment with word boundaries.
#
# We deliberately avoid stop-word normalisation and stemming because the
# old approach collapsed phrases like "on her back" → "back", which then
# matched as a substring of "backdoor" / "backwards" — a strict false
# positive that pushed irrelevant poses to the top.
#
# For ASCII synonyms we use a `\b` word-boundary regex so "back" never
# matches inside "backdoor". Chinese (and other CJK) phrases don't have
# word boundaries — for those we fall back to plain substring matching,
# which is safe because CJK characters don't combine into longer words
# the way Latin letters do.
#
# Each pose_key is treated as an implicit synonym (both with and without
# underscores) so words like "bukkake", "threesome", "blowjob" match even
# though they're not literally in their own synonym list.
import re

_ASCII_RE = re.compile(r"^[\x00-\x7f]+$")


def _is_ascii(text: str) -> bool:
    return bool(_ASCII_RE.match(text))


def _phrase_in_query(synonym: str, query: str) -> bool:
    """True if ``synonym`` occurs in ``query`` with proper word boundaries.

    Both inputs are assumed already-lowered. ASCII phrases use ``\b`` regex;
    CJK phrases use plain substring containment.
    """
    if not synonym:
        return False
    if _is_ascii(synonym):
        # Escape any regex metachars in the synonym (e.g. parentheses)
        pattern = r"(?:^|(?<=\W))" + re.escape(synonym) + r"(?:(?=\W)|$)"
        return re.search(pattern, query) is not None
    return synonym in query


def _candidate_phrases(pose_key: str, synonyms: list[str]) -> list[str]:
    """Build the full phrase list for a pose: synonyms + pose_key variants.

    Always includes ``pose_key`` itself and its space-substituted variant
    so words like ``"blowjob"`` and ``"reverse cowgirl"`` match even if
    not literally listed as synonyms.
    """
    phrases = list(synonyms)
    if pose_key:
        phrases.append(pose_key)
        if "_" in pose_key:
            phrases.append(pose_key.replace("_", " "))
    return phrases


def find_matching_pose_keys(query: str) -> list[tuple[str, int]]:
    """Find all pose_keys whose synonyms appear in ``query``.

    Returns ``[(pose_key, longest_synonym_word_count), ...]`` sorted by
    descending word count (so multi-word matches outrank single-word ones).
    """
    if not query or not query.strip():
        return []

    # Normalise query: lowercase, hyphens to spaces, collapse whitespace
    query_lower = " ".join(query.lower().replace("-", " ").replace(",", " ").split())

    matches: list[tuple[str, int]] = []
    for pose_key, synonyms in POSE_SYNONYMS.items():
        best_word_count = 0
        for phrase in _candidate_phrases(pose_key, synonyms):
            phrase_lower = phrase.lower()
            if not _phrase_in_query(phrase_lower, query_lower):
                continue
            # Word count: number of whitespace-separated tokens. CJK phrases
            # are typically 1 "word" by this metric, which is fine — we want
            # multi-word EN phrases to outrank short noise.
            wc = len(phrase.split()) or 1
            if wc > best_word_count:
                best_word_count = wc
        if best_word_count > 0:
            matches.append((pose_key, best_word_count))

    matches.sort(key=lambda x: x[1], reverse=True)
    return matches


def expand_query(query: str) -> str:
    """Expand a query string with synonyms of any matched pose_key.

    Used to feed richer text into downstream embedding/keyword stages.
    """
    query_lower = query.lower().replace("-", " ")
    expanded_terms: list[str] = [query_lower]
    matches = find_matching_pose_keys(query)
    for pose_key, _ in matches:
        expanded_terms.append(pose_key)
        expanded_terms.extend(POSE_SYNONYMS.get(pose_key, []))
    return " ".join(set(expanded_terms))
