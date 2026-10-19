"""Bounded English evasion matching with source spans for exact exceptions.

This is a deliberately small visual mapping, not UTS #39 confusable conformance
or transliteration of other languages. It is used only at coverage levels 4–5.
"""

from dataclasses import dataclass
from functools import lru_cache
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
VISUAL_MAP.update({"∆":"a", "∧":"a", "©":"c", "¢":"c", "∂":"d", "€":"e",
                   "£":"l", "∩":"n", "®":"r", "†":"t", "∪":"u", "∨":"v", "×":"x", "¥":"y"})
LEET = {"a":"[a4@]", "b":"[b8]", "e":"[e3]", "g":"[g69]", "h":"[h#]",
        "i":"[i1!|]", "l":"[l1!|]", "o":"[o0]", "s":"[s5$]", "t":"[t7+]", "z":"[z2]"}
# Keep a glyph together as one matching unit. Globally replacing punctuation
# would mistake ordinary brackets/math for letters and lose exact source spans.
ASCII_GLYPHS = {
    "a": ("/\\","/-\\"), "b": ("|3",), "c": ("(","<"), "d": ("|)","[)"), "h": ("|-|",),
    "j": ("_|","_/"), "l": ("|_",),
    "k": ("|<","|{"), "m": ("|\\/|","/\\/\\"), "n": ("|\\|","/\\/"),
    "o": ("()","[]","{}","<>"), "p": ("|*","|>"), "r": ("|2","|?"), "t": ("-|-",),
    "u": ("|_|","(_)","\\_/"), "v": ("\\/",), "w": ("\\/\\/",), "x": ("><",), "y": ("\\|/",),
}
MAX_GAP = 64
GAP = rf"[\W_]{{0,{MAX_GAP}}}"
WORD = re.compile(r"\w")
SEPARATOR = re.compile(r"[\W_]")
MASK = re.compile(r"""[^\w\s'"\x60’‘ʼ“”«»]|_""")
FORMATTING = re.compile(r"(?<!\w)(\*{1,3}|_{1,3}|~~|\|\|)(?=\S)[^\n]+?(?<=\S)\1(?!\w)")


@dataclass(frozen=True)
class EvasionText:
    text: str
    spans: tuple[tuple[int,int], ...]

    def source(self, match, original):
        return original[self.spans[match.start()][0]:self.spans[match.end()-1][1]]


def evasion_view(text, *, visual=True, strip_formatting=False):
    """Fold a normalized input, retaining the source of every resulting unit.

    Long runs are capped for matching only. The final retained unit covers the
    whole original run so an allowed plain term cannot exempt its evasions.
    """
    decorations = set()
    if visual and strip_formatting:
        for match in FORMATTING.finditer(text):
            width = len(match.group(1))
            decorations.update(range(match.start(),match.start()+width))
            decorations.update(range(match.end()-width,match.end()))
    characters,spans = [],[]
    repeated = 0
    previous = None
    for index,character in enumerate(text):
        decomposed = " " if index in decorations else unicodedata.normalize("NFKD" if visual else "NFKC",character).casefold()
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


def letter_pattern(character, *, repeated=True):
    pattern = LEET.get(character,re.escape(character))
    if character in ASCII_GLYPHS:
        pattern = "(?:"+"|".join((pattern,*(re.escape(glyph) for glyph in ASCII_GLYPHS[character])))+")"
    return pattern + ("{1,8}" if repeated and (character.isalpha() or character.isdigit()) else "")


@dataclass(frozen=True)
class EvasionMatch:
    first: int
    last: int

    def start(self):
        return self.first

    def end(self):
        return self.last


@dataclass(frozen=True)
class MaskedEvasion:
    """A bounded literal matcher for symbols hiding one or two letters.

    Memoized states avoid compiling every possible masked spelling or allowing
    arbitrary regex backtracking. Symbols cannot stand in for an entire word.
    """

    units: tuple
    maskable: tuple[bool, ...]
    budget: int
    plural: bool

    def finditer(self, text):
        if not MASK.search(text):
            return
        length = len(text)
        # Reject impossible vocabularies before considering gaps or masks. Then
        # start only near a possible first unmasked unit (at most budget units
        # can precede it). Long punctuation runs otherwise create needless states.
        present = tuple(unit.search(text) is not None for unit in self.units)
        if (any(not found and not maskable for found,maskable in zip(present,self.maskable))
                or sum(not found for found,maskable in zip(present,self.maskable) if maskable)>self.budget):
            return
        candidates = bytearray(length)
        for index in range(min(self.budget+1,len(self.units))):
            if index and not self.maskable[index-1]:
                break
            position = 0
            while (match:=self.units[index].search(text,position)) is not None:
                last = match.start()-index
                first = max(0,match.start()-index*(MAX_GAP+8))
                if last>=first:
                    candidates[first:last+1] = b"\1"*(last-first+1)
                position = match.start()+1
        if not any(candidates):
            return
        formatting = bytearray(length)
        for match in FORMATTING.finditer(text):
            width = len(match.group(1))
            formatting[match.start():match.start()+width] = b"\1"*width
            formatting[match.end()-width:match.end()] = b"\1"*width
        # A mask at a word edge must not borrow separators from a neighboring
        # word, or stop inside an obfuscated continuation such as trann!?x.
        clear_left,clear_right = [True]*(length+1),[True]*(length+1)
        for index,character in enumerate(text):
            clear_left[index+1] = character.isspace() or (not WORD.fullmatch(character) and clear_left[index])
        for index in range(length-1,-1,-1):
            character = text[index]
            clear_right[index] = character.isspace() or (not WORD.fullmatch(character) and clear_right[index+1])
        plural_unit = re.compile(letter_pattern("s")) if self.plural else None
        allow_edge_mask = sum(self.maskable)>=6

        @lru_cache(maxsize=None)
        def gaps(position):
            end = position
            while end<min(length,position+MAX_GAP) and SEPARATOR.fullmatch(text[end]):
                end += 1
            return range(position,end+1)

        @lru_cache(maxsize=None)
        def unit_ends(index,position):
            ends = []
            for _ in range(8 if self.maskable[index] else 1):
                match = self.units[index].match(text,position)
                if match is None:
                    break
                position = match.end()
                ends.append(position)
            return ends

        @lru_cache(maxsize=None)
        def mask_ends(position):
            if position>=length or formatting[position] or not MASK.fullmatch(text[position]):
                return ()
            end = position+1
            while end<min(length,position+8) and not formatting[end] and text[end]==text[position]:
                end += 1
            return range(position+1,end+1)

        @lru_cache(maxsize=None)
        def finish(position,masked=False):
            end = position if (position==length or not WORD.fullmatch(text[position])) and (not masked or clear_right[position]) else -1
            if self.plural:
                for start in gaps(position):
                    match = plural_unit.match(text,start)
                    if match is not None:
                        last = match.end()
                        if last==length or not WORD.fullmatch(text[last]):
                            end = max(end,last)
            return end

        @lru_cache(maxsize=None)
        def search(index,position,used):
            if index==len(self.units):
                return finish(position) if used else -1
            best = -1
            for start in (position,) if index==0 else gaps(position):
                for end in unit_ends(index,start):
                    best = max(best,search(index+1,end,used))
                # Unknown edge symbols can be normal punctuation or an adjacent
                # emoji. Only asterisks on longer words are strong enough here.
                edge = index==0 or index==len(self.units)-1
                edge_allowed = not edge or (allow_edge_mask and start<length and text[start]=="*")
                if self.maskable[index] and used<self.budget and edge_allowed and (index or clear_left[start]):
                    for end in mask_ends(start):
                        result = finish(end,True) if index==len(self.units)-1 else search(index+1,end,used+1)
                        best = max(best,result)
            return best

        position = 0
        while position<length:
            if candidates[position] and (position==0 or not WORD.fullmatch(text[position-1])):
                end = search(0,position,0)
                if end>=0:
                    yield EvasionMatch(position,end)
                    position = end
                    continue
            position += 1


def masked_evasion(term, *, plural=False):
    folded = [character for character in evasion_view(term).text if not character.isspace()]
    maskable = tuple(character.isascii() and character.isalpha() for character in folded)
    letters = sum(maskable)
    if letters<4:
        return None
    # Require at least three known letters/shapes, and never permit more than
    # two masked positions. Short configured terms cannot become wildcards.
    budget = min(2,letters-3)
    return MaskedEvasion(tuple(re.compile(letter_pattern(character,repeated=False)) for character in folded),
                         maskable,budget,plural and term[-1].isalpha() and not term.endswith("s"))


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
