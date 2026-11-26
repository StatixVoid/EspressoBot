"""Operator-managed pilot access. Premium never grants Discord permissions."""

from dataclasses import dataclass
import os


PILOT_GUILD_IDS = frozenset({1432165456684519587})
PILOT_USER_IDS = frozenset({926272518275362827})


def configured_ids(name, defaults):
    raw = os.getenv(name)
    if raw is None:
        return defaults
    values = set()
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        if not value.isascii() or not value.isdecimal() or not 0 < int(value) < 2**64:
            raise ValueError(f"{name} must contain comma-separated positive Discord IDs.")
        values.add(int(value))
    return frozenset(values)


@dataclass(frozen=True)
class PremiumConfig:
    enabled: bool = True
    guild_ids: frozenset[int] = PILOT_GUILD_IDS
    user_ids: frozenset[int] = PILOT_USER_IDS

    @classmethod
    def from_environment(cls):
        enabled = os.getenv("PREMIUM_ENABLED", "true").strip().lower()
        if enabled not in {"true", "false"}:
            raise ValueError("PREMIUM_ENABLED must be true or false.")
        return cls(enabled == "true",
                   configured_ids("PREMIUM_GUILD_IDS", PILOT_GUILD_IDS),
                   configured_ids("PREMIUM_USER_IDS", PILOT_USER_IDS))

    def access(self, guild_id: int, user_id: int) -> dict:
        source = None
        if self.enabled:
            if guild_id in self.guild_ids:
                source = "server_allowlist"
            elif user_id in self.user_ids:
                source = "user_allowlist"
        return {"active": source is not None, "source": source,
                "features": ["moderation_insights"] if source else [],
                "billing_enabled": False}
