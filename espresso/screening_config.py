"""Validated, opt-in member screening policy shared by every interface."""

import hashlib
import json


def default_screening():
    return {"enabled":False,"mode":"everyone","minimum_account_age_days":7,
            "verified_role_id":None,"entry_channel_id":None,"require_agreement":True,
            "rules_text":"I agree to follow this server's rules and respect other members."}


def validate_screening(value):
    if type(value) is not dict or set(value) != set(default_screening()):
        raise ValueError("Invalid member screening settings.")
    for key in ("enabled","require_agreement"):
        if type(value[key]) is not bool:
            raise ValueError("Screening switches must be true or false.")
    if value["mode"] not in ("everyone","new_accounts"):
        raise ValueError("Choose review for everyone or new accounts.")
    age = value["minimum_account_age_days"]
    if type(age) is not int or not 1 <= age <= 365:
        raise ValueError("Account age must be 1–365 days.")
    for key in ("verified_role_id","entry_channel_id"):
        if value[key] is not None and (type(value[key]) is not int or not 0 < value[key] < 2**63):
            raise ValueError("Screening roles and channels must be valid server IDs.")
        if value["enabled"] and value[key] is None:
            raise ValueError("Choose a verified role and entry channel before enabling screening.")
    text = value["rules_text"]
    if type(text) is not str or not 1 <= len(text) <= 1800 or not text.strip():
        raise ValueError("Use 1–1800 characters for the verification rules.")


def rules_revision(config):
    return hashlib.sha256(json.dumps({key:config[key] for key in ("rules_text","require_agreement")},sort_keys=True).encode()).hexdigest()


def needs_review(config, created_at, joined_at):
    return config["mode"] == "everyone" or (joined_at - created_at) < config["minimum_account_age_days"] * 86400
