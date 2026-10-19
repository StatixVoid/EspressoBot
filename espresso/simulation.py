"""Pure, side-effect-free explanation of a hypothetical moderation event."""

from .community_config import channel_settings
from .hate_filters import ContentDecision
from .rules import content_decision


def simulate(text, settings, context=None, *, channel_id=None, parent_id=None, timeout_error=None):
    context = context or {}
    keys = {"role_ids","active_warnings","recent_messages","warning_cooldown","edited","administrator","owner","bot"}
    if type(text) is not str or not 1 <= len(text) <= 4000 or type(context) is not dict or not context.keys() <= keys:
        raise ValueError("Supply a sample message and valid simulation context.")
    roles = context.get("role_ids",[])
    if type(roles) is not list or len(roles) > 100 or any(type(role) is not int or role <= 0 for role in roles):
        raise ValueError("Choose up to 100 simulation roles.")
    warnings,recent = context.get("active_warnings",0),context.get("recent_messages",0)
    if type(warnings) is not int or not 0 <= warnings <= 100 or type(recent) is not int or not 0 <= recent <= 50:
        raise ValueError("Use 0–100 active warnings and 0–50 recent messages.")
    for key in keys-{"role_ids","active_warnings","recent_messages"}:
        if key in context and type(context[key]) is not bool:
            raise ValueError("Simulation switches must be booleans.")
    effective = channel_settings(settings,channel_id,parent_id)
    decision = content_decision(text,effective)
    spam = effective.spam_enabled and not context.get("edited",False) and recent+1 >= effective.spam_limit
    if spam and (decision is None or decision.action == "delete"):
        decision = ContentDecision("Message spam")
    result = {"matched":decision is not None,"rule":decision.reason if decision else None,
              "action":decision.action if decision else None,"moderation_enabled":effective.enabled,
              "observation_mode":effective.observation_mode,"effective_action":"none",
              "warnings_after":warnings,"timeout_would_apply":False,"steps":[]}
    steps = result["steps"]
    steps.append("Using the selected channel's overrides and the supplied settings.")
    exempt = set(roles)&set(effective.exempt_roles)
    if context.get("bot") or context.get("owner") or context.get("administrator"):
        steps.append("This member is a bot, server owner, or administrator and bypasses automatic moderation.")
    elif exempt:
        steps.append("Exempt because of role ID(s): "+", ".join(map(str,sorted(exempt)))+".")
    elif not effective.enabled:
        steps.append("Automatic moderation is off for this server or channel.")
    elif decision is None:
        steps.append("No text rule or simulated spam threshold matched.")
    elif effective.observation_mode:
        result["effective_action"] = "observe"
        steps.append(f"Observation mode would log {decision.reason}; no deletion, warning, or timeout.")
    else:
        result["effective_action"] = "delete"
        steps.append(f"Matched {decision.reason}; Espresso would attempt to delete the message.")
        if decision.action != "delete":
            if context.get("warning_cooldown",False):
                steps.append("The automatic warning cooldown would suppress a new warning and its escalation.")
            else:
                result["warnings_after"] = warnings+1
                result["effective_action"] = "warn"
                steps.append(f"A new warning would bring the active count to {warnings+1}.")
                threshold = effective.warn_threshold and warnings+1 >= effective.warn_threshold
                if decision.action == "timeout" or threshold:
                    if timeout_error:
                        steps.append("Timeout would be skipped: "+timeout_error)
                    else:
                        result["effective_action"] = "timeout"
                        result["timeout_would_apply"] = True
                        steps.append(f"Espresso would attempt a {effective.timeout_minutes}-minute timeout; a longer existing timeout is preserved.")
    result["note"] = " ".join(steps)+" This simulation performs no Discord actions. Supplied counts are hypothetical; live message deduplication, timing, and Discord failures can change the outcome."
    return result
