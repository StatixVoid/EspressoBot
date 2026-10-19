"""Render the public feature menu from its small, editable content file."""

from html import escape
import json
from pathlib import Path
import re


CATALOG = Path(__file__).with_name("public") / "features.json"
STATUSES = {"available": "Available", "in_development": "In development", "planned": "Coming soon"}
GROUPS = {"moderation", "welcome", "staff", "community", "other"}


def render_feature_catalog() -> str:
    cards = []
    features = json.loads(CATALOG.read_text(encoding="utf-8"))["features"]
    seen = set()
    for feature in features:
        feature_id, status = feature["id"], feature["status"]
        if not re.fullmatch(r"[a-z][a-z0-9-]*", feature_id) or feature_id in seen:
            raise ValueError("Website feature IDs must be unique lowercase anchor names.")
        if status not in STATUSES:
            raise ValueError("Unknown website feature status.")
        group = feature.get("group", "other")
        if group not in GROUPS:
            raise ValueError("Unknown website feature group.")
        seen.add(feature_id)
        link_html = ""
        link = feature.get("link")
        # Unfinished features can be described, but never presented as usable controls.
        if status == "available" and link:
            href = link["href"]
            if not href.startswith(("/", "#")) or href.startswith("//"):
                raise ValueError("Feature links must stay within the Espresso website.")
            link_html = f'<a class="feature-menu-link" href="{escape(href, quote=True)}">{escape(link["label"])} <span aria-hidden="true">→</span></a>'
        cards.append(
            f'<article class="feature-menu-card" id="{feature_id}" data-feature-status="{status}" data-feature-group="{group}">'
            f'<div class="feature-menu-top"><span class="feature-category">{escape(feature["category"])}</span>'
            f'<span class="feature-availability">{STATUSES[status]}</span></div>'
            f'<h3>{escape(feature["title"])}</h3><p>{escape(feature["description"])}</p>{link_html}</article>'
        )
    return "\n".join(cards)
