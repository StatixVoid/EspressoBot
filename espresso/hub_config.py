"""Validated, independently versioned settings for opt-in community features."""

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def default_hub():
    return {
        "tickets_enabled": False, "ticket_category_id": None, "support_role_id": None,
        "ticket_categories": ["General", "Account help", "Server help"], "ticket_transcripts": False,
        "events_enabled": False, "event_role_id": None,
        "suggestions_enabled": False, "suggestion_channel_id": None,
        "voice_enabled": False, "voice_lobby_id": None, "voice_category_id": None,
        "voice_user_limit": 5, "faq_enabled": False,
        "birthdays_enabled": False, "birthday_channel_id": None, "birthday_role_id": None,
        "timezone": "UTC", "birthday_hour": 9,
        "highlights_enabled": False, "highlight_channel_id": None, "highlight_source_ids": [],
        "highlight_threshold": 3, "profiles_enabled": False,
    }


def identifier(value):
    if type(value) is str and re.fullmatch(r"[1-9][0-9]{0,18}", value):
        value = int(value)
    if type(value) is not int or not 0 < value < 2**63:
        raise ValueError("Choose a valid server, member, role, or channel ID.")
    return value


def text(value, label, low, high):
    if type(value) is not str or not low <= len(value.strip()) <= high:
        raise ValueError(f"{label} needs {low}–{high} characters.")
    return value.strip()


def validate_hub(raw):
    defaults = default_hub()
    if type(raw) is not dict or not raw.keys() <= defaults.keys():
        raise ValueError("Unknown community hub settings.")
    result = defaults | raw
    for key, default in defaults.items():
        value = result[key]
        if type(default) is bool and type(value) is not bool:
            raise ValueError(f"{key} must be enabled or disabled.")
        if key.endswith("_id") and value is not None:
            result[key] = identifier(value)
    for key, low, high in (("voice_user_limit", 1, 99), ("birthday_hour", 0, 23), ("highlight_threshold", 2, 50)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f"{key} must be between {low} and {high}.")
    categories = result["ticket_categories"]
    if type(categories) is not list or not 1 <= len(categories) <= 10:
        raise ValueError("Choose 1–10 support categories.")
    result["ticket_categories"] = list(dict.fromkeys(text(item, "Category", 1, 40) for item in categories))
    sources = result["highlight_source_ids"]
    if type(sources) is not list or len(sources) > 25:
        raise ValueError("Choose up to 25 highlight source channels.")
    result["highlight_source_ids"] = list(dict.fromkeys(identifier(item) for item in sources))
    if type(result["timezone"]) is not str or len(result["timezone"]) > 100:
        raise ValueError("Choose an IANA timezone, such as America/Phoenix.")
    try:
        ZoneInfo(result["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Choose an IANA timezone, such as America/Phoenix.") from None
    required = {
        "tickets": ("ticket_category_id", "support_role_id"), "voice": ("voice_lobby_id", "voice_category_id"),
        "suggestions": ("suggestion_channel_id",), "birthdays": ("birthday_channel_id",),
        "highlights": ("highlight_channel_id",),
    }
    for feature, keys in required.items():
        if result[feature + "_enabled"] and any(result[key] is None for key in keys):
            raise ValueError(f"Complete the {feature} channels and roles before enabling it.")
    if result["highlights_enabled"] and (not sources or result["highlight_channel_id"] in result["highlight_source_ids"]):
        raise ValueError("Choose highlight source channels separate from the highlight board.")
    return result


SUGGESTION_STATUSES = ("open", "under_review", "accepted", "planned", "completed", "declined")
THEMES = {"rose": 0xD8A7B1, "lavender": 0xB8A1DB, "mocha": 0x98725C}
