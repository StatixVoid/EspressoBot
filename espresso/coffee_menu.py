"""The café menu shared by Discord cards and the website."""

import json
from pathlib import Path


PUBLIC_ASSETS = Path(__file__).with_name("public")
_catalog = json.loads((PUBLIC_ASSETS / "drinks.json").read_text(encoding="utf-8"))
DRINKS = tuple(_catalog["drinks"])
CHARACTERS = tuple(_catalog["characters"])
DRINK_BY_ID = {drink["id"]: drink for drink in DRINKS}
CHARACTER_BY_ID = {character["id"]: character for character in CHARACTERS}
CHARACTER_ASSETS = {
    "sabrina": "sabrina-card-v1.png",
    "guardian": "cafe-guardian-card-v1.png",
    "dreamer": "cafe-dreamer-card-v1.png",
    "spark": "cafe-spark-card-v1.png",
}

# Artwork always resolves through this allowlist, never through a member's path or URL.
if len(DRINK_BY_ID) != len(DRINKS) or set(CHARACTER_BY_ID) != set(CHARACTER_ASSETS) | {"none"}:
    raise ValueError("The café catalog has duplicate drinks or invalid characters.")
for _character_id, _filename in CHARACTER_ASSETS.items():
    if CHARACTER_BY_ID[_character_id]["image"] != f"/assets/{_filename}":
        raise ValueError("Café artwork must use a bundled character asset.")


def drink_choice(value):
    """Validate a stable menu ID before a send can consume its cooldown."""
    if value == "surprise":
        return None
    if type(value) is not str or value not in DRINK_BY_ID:
        raise ValueError("Choose a drink from /drinks, or use surprise.")
    return DRINK_BY_ID[value]


def character_choice(value):
    if type(value) is not str or value not in CHARACTER_BY_ID:
        raise ValueError("Choose Sabrina, Velvet Guardian, Cloud Dreamer, Honey Spark, or just the drink.")
    return CHARACTER_BY_ID[value]


def character_asset(character_id):
    """Return a known local artwork path, or no artwork for a simple card."""
    character_choice(character_id)
    filename = CHARACTER_ASSETS.get(character_id)
    return PUBLIC_ASSETS / filename if filename else None
