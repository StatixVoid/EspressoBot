"""English starter rules for moderation. No identity or intent inference.

Terms below are offensive examples included solely to detect and moderate abuse.
The lists are deliberately small, inspectable, cumulative, and administrator-tunable.
"""

from dataclasses import dataclass
import re
import unicodedata

CATEGORIES = {"racism": "Racism", "misogyny": "Misogyny", "trans_hate": "Anti-trans hate"}
ACTIONS = {"delete", "warn", "timeout"}
LEVELS = {
    1: "Core explicit slurs",
    2: "Expanded slur vocabulary",
    3: "Discriminatory and exclusionary phrases",
    4: "Common spaced, punctuated, and leetspeak slur evasions",
    5: "Broader identity-directed insults and stereotypes",
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


def boundary(pattern: str) -> str:
    return r"(?<!\w)(?:" + pattern + r")(?!\w)"


def literal_pattern(term: str) -> str:
    # Match simple English plurals without widening partial-word matches.
    return re.escape(term) + (r"s?" if term[-1].isalpha() and not term.endswith("s") else "")


LEET = {"a": "[a4@]", "e": "[e3]", "i": "[i1!]", "l": "[l1]", "o": "[o0]", "s": "[s5$]", "t": "[t7]"}


def evasion_pattern(term: str) -> str:
    return r"[\W_]{0,3}".join(LEET.get(char, re.escape(char)) for char in term) + r"s?"


def detect(text: str, filters: dict) -> ContentDecision | None:
    matches = []
    for category, config in filters.items():
        if not config["enabled"]:
            continue
        allowed = {text_key(v).strip() for v in config["allowed_terms"]}
        patterns = []
        for minimum, terms in TERMS[category].items():
            if config["level"] >= minimum:
                patterns.extend(literal_pattern(term) for term in terms)
                if config["level"] >= 4:
                    patterns.extend(evasion_pattern(term) for term in terms)
        for minimum, rules in PATTERNS[category].items():
            if config["level"] >= minimum:
                patterns.extend(rules)
        patterns.extend(re.escape(text_key(term).strip()) for term in config["extra_terms"])
        for pattern in patterns:
            if any(text_key(match.group()).strip() not in allowed for match in re.finditer(boundary(pattern), text)):
                matches.append(ContentDecision(f"{CATEGORIES[category]} filter · level {config['level']}", config["action"], category))
                break
    # If several categories match, enforce the strongest configured action.
    return max(matches, key=lambda match: {"delete":0,"warn":1,"timeout":2}[match.action], default=None)
