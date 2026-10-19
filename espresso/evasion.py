"""Bounded English evasion matching with source spans for exact exceptions.

This is a deliberately small visual mapping, not UTS #39 confusable conformance
or transliteration of other languages. It is used only at coverage levels 4–5.
"""

from dataclasses import dataclass
import re
import unicodedata


LOOKALIKES = {
    "a": "аαɑ", "b": "Ьь", "c": "сϲ", "e": "еεє", "h": "һ",
    "i": "іιı", "j": "ј", "k": "κк", "l": "ӏ", "n": "ո",
    "o": "оοօ", "p": "рρ", "r": "гʀ", "s": "ѕ", "t": "тτ",
    "v": "νѵ", "x": "хχ", "y": "уυ",
}
VISUAL_MAP = {character:replacement for replacement,characters in LOOKALIKES.items() for character in characters}
VISUAL_MAP.update({"’":"'", "‘":"'", "ʼ":"'"})
LEET = {"a":"[a4@]", "b":"[b8]", "e":"[e3]", "g":"[g69]", "h":"[h#]",
        "i":"[i1!|]", "l":"[l1!|]", "o":"[o0]", "s":"[s5$]", "t":"[t7+]", "z":"[z2]"}
GAP = r"[\W_]{0,12}"


@dataclass(frozen=True)
class EvasionText:
    text: str
    spans: tuple[tuple[int,int], ...]

    def source(self, match, original):
        return original[self.spans[match.start()][0]:self.spans[match.end()-1][1]]


def evasion_view(text, *, visual=True):
    """Fold a normalized input, retaining the source of every resulting unit.

    Long runs are capped for matching only. The final retained unit covers the
    whole original run so an allowed plain term cannot exempt its evasions.
    """
    characters,spans = [],[]
    repeated = 0
    previous = None
    for index,character in enumerate(text):
        decomposed = unicodedata.normalize("NFKD" if visual else "NFKC",character).casefold()
        for unit in decomposed:
            if unicodedata.category(unit) in (("Mn","Me","Cf") if visual else ("Cf",)):
                if spans:
                    spans[-1] = (spans[-1][0],index+1)
                continue
            unit = VISUAL_MAP.get(unit,unit) if visual else unit
            repeated = repeated+1 if unit==previous else 1
            previous = unit
            if visual and repeated>8:
                spans[-1] = (spans[-1][0],index+1)
                continue
            characters.append(unit)
            spans.append((index,index+1))
    return EvasionText("".join(characters),tuple(spans))


def unicode_disguise(source):
    """Unicode inside a matched span, excluding ordinary quotes and whitespace.

    This records a matching transformation, never a claim about a user's intent.
    Unicode elsewhere in the message does not enter this check.
    """
    for character in source:
        if character.isascii() or character.isspace() or character in "’‘ʼ“”«»":
            continue
        if unicodedata.category(character) in ("Cf","Mn","Me") or character in VISUAL_MAP:
            return True
        decomposed = unicodedata.normalize("NFKD",character).casefold()
        if decomposed != character.casefold() and any(c.isascii() and c.isalnum() for c in decomposed):
            return True
        if unicodedata.category(character)[0] in ("P","S"):
            return True
    return False


def letter_pattern(character):
    return LEET.get(character,re.escape(character)) + ("{1,8}" if character.isalpha() or character.isdigit() else "")


def literal_evasion(term, *, plural=False):
    folded = evasion_view(term).text
    units = [letter_pattern(character) for character in folded if not character.isspace()]
    if not units or not any(character.isalnum() for character in folded):
        return None
    result = GAP.join(units)
    if plural and term[-1].isalpha() and not term.endswith("s"):
        result += "(?:"+GAP+letter_pattern("s")+")?"
    return result


def phrase_evasion(pattern):
    """Compile our fixed phrase grammar without rewriting regex syntax as text.

    The starter grammar consists of letters, spaces/apostrophes, alternatives,
    and optional noncapturing groups. Configured terms never enter this parser.
    """
    def sequence(index,initial):
        output = []
        first = initial
        while index<len(pattern) and pattern[index] not in "|)":
            if pattern.startswith("(?:",index):
                alternatives = []
                index += 3
                while True:
                    branch,index = sequence(index,first)
                    alternatives.append(branch)
                    if index>=len(pattern):
                        raise ValueError("Unclosed starter phrase group.")
                    if pattern[index]==")":
                        index += 1
                        break
                    index += 1
                optional = index<len(pattern) and pattern[index]=="?"
                if optional:
                    if first:
                        raise ValueError("Starter phrases cannot start with an optional group.")
                    index += 1
                output.append("(?:"+"|".join(alternatives)+")"+("?" if optional else ""))
                first = False
                continue
            character = pattern[index]
            if character in " '’":
                index += 1
                continue
            if not character.isascii() or not character.isalpha():
                raise ValueError("Unsupported starter phrase syntax.")
            output.append(("" if first else GAP)+letter_pattern(character))
            first = False
            index += 1
        return "".join(output),index

    result,index = sequence(0,True)
    if index!=len(pattern):
        raise ValueError("Unexpected starter phrase group boundary.")
    return result
