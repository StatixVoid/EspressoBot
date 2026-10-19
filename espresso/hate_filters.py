"""English starter rules for moderation. No identity or intent inference.

Terms below are offensive examples included solely to detect and moderate abuse.
The lists are deliberately small, inspectable, cumulative, and administrator-tunable.
"""

from dataclasses import dataclass
from functools import lru_cache
import re
import unicodedata

from .evasion import evasion_view, literal_evasion, phrase_evasion, unicode_disguise

CATEGORIES = {"racism": "Racism", "misogyny": "Misogyny", "trans_hate": "Anti-trans hate"}
ACTIONS = {"delete", "warn", "timeout"}
LEVELS = {
    1: "Core explicit slurs",
    2: "Expanded slur vocabulary",
    3: "Discriminatory and exclusionary phrases",
    4: "Spacing, punctuation, leetspeak, repetition, and Unicode evasions of slurs and phrases",
    5: "Broader identity-directed insults and stereotypes, including their evasions",
}

# Level 2 includes level 1. Higher levels add patterns, not stronger penalties.
TERMS = {
    "racism": {1: ("nigger", "nigga", "kike"),
               2: ("chink", "spic", "raghead", "paki")},
    "misogyny": {1: ("cunt", "femoid"),
                  2: ("bitch", "slut", "whore", "feminazi")},
    "trans_hate": {1: ("tranny", "shemale"),
                    2: ("troon", "trannies", "he-she")},
}
GROUP = r"(?:black|white|asian|latino|latina|hispanic|arab|jewish|indigenous) (?:people|men|women)"
PATTERNS = {
    "racism": {
        3: (rf"{GROUP} are (?:inferior|subhuman)",
            r"no (?:black|white|asian|hispanic|arab|jewish) (?:people )?allowed"),
        5: (rf"{GROUP} are (?:all )?(?:criminals|vermin|animals)",
            r"(?:black|white|asian|hispanic|arab|jewish) (?:people|men|women) don't belong here"),
    },
    "misogyny": {
        3: (r"women belong in (?:the )?kitchen", r"(?:women|girls) are (?:inferior|property|subhuman)",
            r"(?:women|girls) (?:should not|shouldn't) (?:vote|work|have rights)"),
        5: (r"(?:women|girls) are (?:all )?(?:stupid|useless|worthless)",
            r"(?:women|girls) (?:can't|cannot) (?:think|lead|code)"),
    },
    "trans_hate": {
        3: (r"trans(?:gender)? people are (?:subhuman|inferior)",
            r"no trans(?:gender)? (?:people )?allowed", r"trans(?:gender)? people (?:should not|shouldn't) have rights"),
        5: (r"trans(?:gender)? people are (?:all )?(?:predators|freaks|mentally ill)",
            r"trans(?:gender)? people don't belong here"),
    },
}


def text_key(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold()
                   if unicodedata.category(c) != "Cf")


def default_filters() -> dict:
    return {category: {"enabled": False, "level": 1, "action": "warn", "extra_terms": [], "allowed_terms": []}
            for category in CATEGORIES}


def validate_filters(filters) -> None:
    if type(filters) is not dict or filters.keys() != CATEGORIES.keys():
        raise ValueError("Configure racism, misogyny, and trans_hate separately.")
    for config in filters.values():
        if type(config) is not dict or set(config) != {"enabled", "level", "action", "extra_terms", "allowed_terms"}:
            raise ValueError("Invalid hate-filter configuration.")
        if type(config["enabled"]) is not bool or type(config["level"]) is not int or not 1 <= config["level"] <= 5:
            raise ValueError("Each category needs an on/off switch and a level from 1 to 5.")
        if type(config["action"]) is not str or config["action"] not in ACTIONS:
            raise ValueError("Choose delete, warn, or timeout as the category action.")
        for key in ("extra_terms", "allowed_terms"):
            values = config[key]
            if (type(values) is not list or len(values) > 100
                    or any(type(v) is not str or not 1 <= len(v) <= 80 or not text_key(v).strip() for v in values)):
                raise ValueError("Category term lists support up to 100 visible phrases of 1–80 characters.")


@dataclass(frozen=True)
class ContentDecision:
    reason: str
    action: str = "warn"
    category: str | None = None
    unicode_evasion: bool = False


def boundary(pattern: str) -> str:
    return r"(?<!\w)(?:" + pattern + r")(?!\w)"


def literal_pattern(term: str) -> str:
    # Match simple English plurals without widening partial-word matches.
    return re.escape(term) + (r"s?" if term[-1].isalpha() and not term.endswith("s") else "")


def evasion_pattern(term: str) -> str:
    return literal_evasion(text_key(term),plural=True)


@lru_cache(maxsize=15)
def starter_patterns(category,level):
    literals,evasions = [],[]
    for minimum,terms in TERMS[category].items():
        if level>=minimum:
            literals.extend(literal_pattern(term) for term in terms)
            if level>=4:
                evasions.extend(evasion_pattern(term) for term in terms)
    for minimum,patterns in PATTERNS[category].items():
        if level>=minimum:
            literals.extend(patterns)
            if level>=4:
                evasions.extend(phrase_evasion(pattern) for pattern in patterns)
    return tuple(re.compile(boundary(pattern)) for pattern in literals),tuple(re.compile(boundary(pattern)) for pattern in evasions)


@lru_cache(maxsize=1024)
def extra_pattern(term,evasive=False):
    pattern = literal_evasion(term) if evasive else re.escape(term)
    return re.compile(boundary(pattern)) if pattern else None


def detect(text: str, filters: dict) -> ContentDecision | None:
    original = text
    text = text_key(text)
    folded = None
    normalized = None
    matches = []
    for category, config in filters.items():
        if not config["enabled"]:
            continue
        allowed = {text_key(v).strip() for v in config["allowed_terms"]}
        explicit_unicode = {unicodedata.normalize("NFC",term).casefold().strip() for term in config["extra_terms"] if not term.isascii()}
        def disguised_source(source):
            return unicode_disguise(source) and unicodedata.normalize("NFC",source).casefold().strip() not in explicit_unicode
        literal,evasive = starter_patterns(category,config["level"])
        extras = tuple(text_key(term).strip() for term in config["extra_terms"])
        literal += tuple(extra_pattern(term) for term in extras)
        matched = any(match.group().strip() not in allowed for pattern in literal for match in pattern.finditer(text))
        disguised = False
        if matched and not original.isascii():
            if normalized is None:
                normalized = evasion_view(original,visual=False)
            # Canonical composition can cross code points. Do not guess a source
            # span when that happens; the level 4–5 visual pass handles it below.
            if normalized.text == text:
                disguised = any(text_key(normalized.source(match,original)).strip() not in allowed and disguised_source(normalized.source(match,original))
                                for pattern in literal for match in pattern.finditer(normalized.text))
        if not disguised and config["level"]>=4:
            if folded is None:
                folded = evasion_view(original)
            evasive += tuple(pattern for term in extras if (pattern:=extra_pattern(term,True)) is not None)
            for pattern in evasive:
                for match in pattern.finditer(folded.text):
                    source = folded.source(match,original)
                    if text_key(source).strip() in allowed:
                        continue
                    matched = True
                    if disguised_source(source):
                        disguised = True
                        break
                if disguised:
                    break
        if matched:
            reason = f"{CATEGORIES[category]} filter · level {config['level']}" + (" · Unicode evasion" if disguised else "")
            matches.append(ContentDecision(reason,"timeout" if disguised else config["action"],category,disguised))
    # If several categories match, enforce the strongest configured action.
    return max(matches, key=lambda match: ({"delete":0,"warn":1,"timeout":2}[match.action],match.unicode_evasion), default=None)
