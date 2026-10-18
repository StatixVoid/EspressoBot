"""Pure filtering and bounded, in-memory spam detection. No Discord dependency."""

from collections import OrderedDict, deque
from dataclasses import dataclass, field
import re
import unicodedata

from .hate_filters import ContentDecision, default_filters, detect, validate_filters
from .reaction_roles import validate_mappings


def normalize(text: str) -> str:
    # NFKC catches full-width text; ignore invisible format characters.
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold()
                   if unicodedata.category(c) != "Cf")


INVITE = re.compile(
    r"(?<![\w.-])(?:https?://)?(?:www\.)?"
    r"(?:discord\.gg/|discord(?:app)?\.com/invite/)[a-z0-9-]+", re.I
)
# Deliberately conservative: protocols, www, and ordinary bare domains.
# This is a text filter, not a URL safety scanner or a DNS lookup.
LINK = re.compile(
    r"(?:https?://|www\.)\S+|(?<![\w@.-])"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}(?![\w-])", re.I
)


@dataclass
class Settings:
    enabled: bool = False
    spam_enabled: bool = True
    spam_limit: int = 6
    spam_window: int = 8
    block_links: bool = False
    block_invites: bool = True
    bad_words: list[str] = field(default_factory=list)
    exempt_roles: list[int] = field(default_factory=list)
    log_channel: int | None = None
    warn_threshold: int = 3
    warn_window_hours: int = 24
    timeout_minutes: int = 10
    hate_filters: dict = field(default_factory=default_filters)
    reaction_roles_enabled: bool = False
    reaction_roles: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        for name in ("enabled", "spam_enabled", "block_links", "block_invites", "reaction_roles_enabled"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        for name, low, high in (("spam_limit", 2, 50), ("spam_window", 2, 60),
                                ("warn_threshold", 0, 20), ("warn_window_hours", 1, 720),
                                ("timeout_minutes", 1, 40320)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer from {low} to {high}")
        if (type(self.bad_words) is not list or len(self.bad_words) > 100
                or any(type(w) is not str or not 1 <= len(w) <= 80 or not normalize(w).strip()
                       for w in self.bad_words)):
            raise ValueError("bad_words must contain at most 100 visible phrases of 1–80 characters")
        if (type(self.exempt_roles) is not list or len(self.exempt_roles) > 100
                or any(type(r) is not int or r <= 0 for r in self.exempt_roles)):
            raise ValueError("exempt_roles must contain at most 100 positive role IDs")
        if self.log_channel is not None and (type(self.log_channel) is not int or self.log_channel <= 0):
            raise ValueError("log_channel must be a positive channel ID or null")
        validate_filters(self.hate_filters)
        validate_mappings(self.reaction_roles)


def _basic_decision(content: str, settings: Settings) -> ContentDecision | None:
    text = normalize(content)
    if settings.block_invites and INVITE.search(text):
        return ContentDecision("Discord invite")
    if settings.block_links and LINK.search(text):
        return ContentDecision("Link")
    for word in settings.bad_words:
        # Literals with word boundaries: 'class' does not match blocked 'ass'.
        if re.search(r"(?<!\w)" + re.escape(normalize(word)) + r"(?!\w)", text):
            return ContentDecision("Blocked word or phrase")
    return None


def content_decision(content: str, settings: Settings) -> ContentDecision | None:
    matches = [match for match in (_basic_decision(content, settings), detect(normalize(content), settings.hate_filters)) if match]
    return max(matches, key=lambda match: ({"delete":0,"warn":1,"timeout":2}[match.action], match.category is not None), default=None)


def content_violation(content: str, settings: Settings) -> str | None:
    decision = content_decision(content, settings)
    return decision.reason if decision else None


class SpamTracker:
    def __init__(self, capacity: int = 10_000):
        self.capacity = capacity
        self.buckets: OrderedDict[tuple[int, int], deque[float]] = OrderedDict()

    def hit(self, guild_id: int, user_id: int, now: float, limit: int, window: int) -> bool:
        key = (guild_id, user_id)
        bucket = self.buckets.pop(key, deque(maxlen=50))
        while bucket and bucket[0] <= now - window:
            bucket.popleft()
        bucket.append(now)
        self.buckets[key] = bucket
        while len(self.buckets) > self.capacity:
            self.buckets.popitem(last=False)
        return len(bucket) >= limit

    def reset_guild(self, guild_id: int) -> None:
        for key in list(self.buckets):
            if key[0] == guild_id:
                del self.buckets[key]
