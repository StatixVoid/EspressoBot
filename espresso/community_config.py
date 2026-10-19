"""Validated, opt-in community settings shared by Discord and the panel."""

from dataclasses import replace
import re
from urllib.parse import urlsplit


def default_raid():
    return {"enabled": False, "join_limit": 10, "window_seconds": 20,
            "repeat_limit": 5, "action": "alert", "slowmode_seconds": 15,
            "duration_minutes": 10, "excluded_channel_ids": [], "excluded_category_ids": []}


def default_welcome():
    return {"enabled": False, "channel_id": None,
            "message": "Welcome {member} to {server}! Grab a coffee and make yourself at home. ☕"}


def default_community():
    return {"coffee_enabled": False, "polls_enabled": False,
            "prompts_enabled": False, "prompt_channel_id": None,
            "prompt_hour_utc": 16, "notification_role_id": None,
            "coffee_style": "classic", "coffee_title": "A little pick-me-up ☕",
            "coffee_message": "{sender} brought {member} {drink}.\nMade with a little kindness.",
            "coffee_color": "D8A7B1", "prompt_library": []}


def integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}.")


def boolean(value, name):
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean.")


def discord_id(value, name):
    if value is not None:
        integer(value, 1, 2**63 - 1, name)


def domain(value):
    if type(value) is not str or len(value) > 253 or value != value.lower().strip():
        raise ValueError("Use lowercase domain names, without URLs or paths.")
    if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", value):
        raise ValueError("Use domain names such as example.com, without URLs or paths.")
    return value


def validate_extensions(settings):
    # Older persisted policies inherit additions; unknown keys still fail validation below.
    if type(settings.raid) is dict:
        settings.raid = default_raid() | settings.raid
    if type(settings.community) is dict:
        settings.community = default_community() | settings.community
    boolean(settings.observation_mode, "Observation mode")
    integer(settings.warning_expiry_days, 0, 3650, "Warning expiry days")
    if type(settings.allowed_domains) is not list or len(settings.allowed_domains) > 100:
        raise ValueError("Choose up to 100 trusted domains.")
    for item in settings.allowed_domains:
        domain(item)
    if type(settings.channel_rules) is not list or len(settings.channel_rules) > 100:
        raise ValueError("Choose up to 100 channel rules.")
    seen = set()
    for rule in settings.channel_rules:
        if type(rule) is not dict or set(rule) != {"channel_id", "enabled", "block_links", "block_invites", "spam_enabled"}:
            raise ValueError("Invalid channel rule.")
        discord_id(rule["channel_id"], "Channel")
        if rule["channel_id"] is None or rule["channel_id"] in seen:
            raise ValueError("Choose each channel once.")
        seen.add(rule["channel_id"])
        boolean(rule["enabled"], "Channel moderation")
        for key in ("block_links", "block_invites", "spam_enabled"):
            if rule[key] is not None:
                boolean(rule[key], key)
    raid = settings.raid
    if type(raid) is not dict or set(raid) != set(default_raid()):
        raise ValueError("Invalid raid settings.")
    boolean(raid["enabled"], "Raid protection")
    for key, low, high in (("join_limit", 3, 100), ("window_seconds", 5, 120),
                           ("repeat_limit", 3, 50), ("slowmode_seconds", 1, 21600),
                           ("duration_minutes", 1, 60)):
        integer(raid[key], low, high, key)
    if raid["action"] not in ("alert", "slowmode", "lockdown"):
        raise ValueError("Choose alert, slowmode, or lockdown.")
    for key in ("excluded_channel_ids", "excluded_category_ids"):
        if type(raid[key]) is not list or len(raid[key]) > 100:
            raise ValueError("Choose up to 100 unique raid exclusions per list.")
        for item in raid[key]:
            discord_id(item, "Raid exclusion")
            if item is None:
                raise ValueError("Choose a valid raid exclusion.")
        if len(set(raid[key])) != len(raid[key]):
            raise ValueError("Choose each raid exclusion once.")
    welcome = settings.welcome
    if type(welcome) is not dict or set(welcome) != set(default_welcome()):
        raise ValueError("Invalid welcome settings.")
    boolean(welcome["enabled"], "Welcomes")
    discord_id(welcome["channel_id"], "Welcome channel")
    if type(welcome["message"]) is not str or not 1 <= len(welcome["message"].strip()) <= 1500:
        raise ValueError("Welcome messages must contain 1–1500 characters.")
    if welcome["enabled"] and welcome["channel_id"] is None:
        raise ValueError("Choose a welcome channel.")
    community = settings.community
    if type(community) is not dict or set(community) != set(default_community()):
        raise ValueError("Invalid community settings.")
    for key in ("coffee_enabled", "polls_enabled", "prompts_enabled"):
        boolean(community[key], key)
    for key in ("prompt_channel_id", "notification_role_id"):
        discord_id(community[key], key)
    integer(community["prompt_hour_utc"], 0, 23, "Daily prompt hour (UTC)")
    if community["prompts_enabled"] and community["prompt_channel_id"] is None:
        raise ValueError("Choose a daily prompt channel.")
    if community["coffee_style"] not in ("classic", "pastel", "midnight", "custom"):
        raise ValueError("Choose a coffee card style.")
    if type(community["coffee_title"]) is not str or not 1 <= len(community["coffee_title"].strip()) <= 100:
        raise ValueError("Coffee titles need 1–100 characters.")
    if type(community["coffee_message"]) is not str or not 1 <= len(community["coffee_message"].strip()) <= 1000:
        raise ValueError("Coffee messages need 1–1000 characters.")
    if type(community["coffee_color"]) is not str or not re.fullmatch(r"[A-Fa-f0-9]{6}",community["coffee_color"]):
        raise ValueError("Choose a six-digit coffee card color.")
    prompts = community["prompt_library"]
    if type(prompts) is not list or len(prompts) > 100 or any(type(item) is not str or not 1 <= len(item.strip()) <= 300 for item in prompts):
        raise ValueError("Use up to 100 prompts of 1–300 characters each.")


def channel_settings(settings, channel_id, parent_id=None):
    """A thread inherits its parent's overrides unless it has its own rule."""
    by_id = {rule["channel_id"]: rule for rule in settings.channel_rules}
    rule = by_id.get(channel_id, by_id.get(parent_id))
    if rule is None:
        return settings
    changes = {key: rule[key] for key in ("block_links", "block_invites", "spam_enabled") if rule[key] is not None}
    if not rule["enabled"]:
        changes["enabled"] = False
    return replace(settings, **changes)


def trusted_link(value, allowed):
    try:
        host = urlsplit(value if "://" in value else "https://" + value).hostname
        host = (host or "").lower().rstrip(".")
        return any(host == item or host.endswith("." + item) for item in allowed)
    except ValueError:
        return False
