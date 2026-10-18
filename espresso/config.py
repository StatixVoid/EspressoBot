"""Project-local .env loading and validated runtime configuration."""

from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def load_environment() -> None:
    # A host's environment wins. Never search parent folders for secret files.
    load_dotenv(ROOT / ".env", override=False, encoding="utf-8-sig")


@dataclass(frozen=True)
class WebConfig:
    base_url: str = "http://localhost:8080"
    host: str = "127.0.0.1"
    port: int = 8080
    client_id: str = ""
    client_secret: str = ""

    def __post_init__(self):
        url = urlsplit(self.base_url)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username
                or url.password or url.path not in {"", "/"} or url.query or url.fragment):
            raise ValueError("WEB_BASE_URL must be a plain HTTP(S) origin, without a path or credentials.")
        if url.scheme != "https" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("WEB_BASE_URL must use HTTPS outside localhost.")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("WEB_PORT must be an integer from 1 to 65535.")
        if self.client_id and not self.client_id.isdecimal():
            raise ValueError("DISCORD_CLIENT_ID must be a numeric application ID.")

    @property
    def oauth_ready(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def secure(self) -> bool:
        return self.base_url.startswith("https://")

    @property
    def callback_url(self) -> str:
        return self.base_url.rstrip("/") + "/auth/discord/callback"

    @classmethod
    def from_environment(cls):
        try:
            port = int(os.getenv("WEB_PORT", "8080"))
        except ValueError:
            raise ValueError("WEB_PORT must be an integer.") from None
        return cls(base_url=os.getenv("WEB_BASE_URL", "http://localhost:8080").rstrip("/"),
                   host=os.getenv("WEB_HOST", "127.0.0.1"), port=port,
                   client_id=os.getenv("DISCORD_CLIENT_ID", "").strip(),
                   client_secret=os.getenv("DISCORD_CLIENT_SECRET", "").strip())
