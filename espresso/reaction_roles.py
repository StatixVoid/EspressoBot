"""Validation shared by the panel, slash commands, and reaction event handlers."""

import re
import unicodedata

SENSITIVE_PERMISSIONS = (
    "administrator", "manage_guild", "manage_roles", "moderate_members", "ban_members",
    "kick_members", "manage_channels", "manage_messages", "manage_webhooks", "manage_threads",
    "manage_events", "mention_everyone",
    "manage_nicknames", "view_audit_log", "manage_emojis_and_stickers", "manage_expressions",
    "manage_guild_expressions", "manage_permissions", "view_guild_insights", "manage_guild_monetization",
)
CUSTOM_EMOJI = re.compile(r"<a?:[A-Za-z0-9_]{2,32}:([0-9]{1,20})>")


def emoji_key(value: str) -> str:
    custom = CUSTOM_EMOJI.fullmatch(value)
    if custom:
        return "custom:" + custom[1]
    return "unicode:" + value.replace("\ufe0f", "")


def validate_mappings(mappings) -> None:
    if type(mappings) is not list or len(mappings) > 25:
        raise ValueError("Use at most 25 reaction-role mappings per server.")
    seen, roles = set(), set()
    for mapping in mappings:
        if type(mapping) is not dict or set(mapping) != {"channel_id", "message_id", "emoji", "role_id", "remove_on_unreact"}:
            raise ValueError("Invalid reaction-role mapping.")
        for key in ("channel_id", "message_id", "role_id"):
            if type(mapping[key]) is not int or not 0 < mapping[key] < 2**63:
                raise ValueError("Reaction-role IDs must be positive Discord IDs.")
        emoji = mapping["emoji"]
        if type(emoji) is not str or not 1 <= len(emoji) <= 64 or any(c.isspace() for c in emoji):
            raise ValueError("Choose a Unicode emoji or a Discord custom emoji.")
        if not CUSTOM_EMOJI.fullmatch(emoji) and not any(unicodedata.category(c) in {"So", "Sk"} or c == "\u20e3" for c in emoji):
            raise ValueError("Choose an emoji, not a word or role mention.")
        if type(mapping["remove_on_unreact"]) is not bool:
            raise ValueError("remove_on_unreact must be a boolean.")
        trigger = (mapping["message_id"], emoji_key(emoji))
        if trigger in seen or mapping["role_id"] in roles:
            raise ValueError("Each reaction trigger and role may appear in only one mapping.")
        seen.add(trigger)
        roles.add(mapping["role_id"])


def mapping_key(mapping: dict) -> str:
    return f"{mapping['channel_id']}:{mapping['message_id']}:{emoji_key(mapping['emoji'])}:{mapping['role_id']}"


def role_error(role, guild, me, actor=None) -> str | None:
    if role is None or role.is_default() or role.managed:
        return "Choose a normal server role, not everyone or an integration-managed role."
    if any(getattr(role.permissions, flag, False) for flag in SENSITIVE_PERMISSIONS):
        return "Reaction roles cannot grant staff, administration, or mass-mention permissions."
    if me is None or not me.guild_permissions.manage_roles or me.top_role <= role:
        return "Espresso needs Manage Roles and a highest role above the reaction role."
    if actor is not None:
        if not actor.guild_permissions.manage_roles:
            return "You need Manage Roles to configure reaction roles."
        if actor.id != guild.owner_id and actor.top_role <= role:
            return "Your highest role must be above the reaction role."
    return None
