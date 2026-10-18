import os
import unittest
from unittest.mock import patch

from espresso.__main__ import main


class EntrypointTests(unittest.TestCase):
    def test_missing_token_fails_before_any_login(self):
        with patch.dict(os.environ, {}, clear=True), patch("espresso.__main__.load_environment"), patch("sys.argv", ["espresso", "--bot-only"]):
            with self.assertRaisesRegex(SystemExit, "Set DISCORD_TOKEN"):
                main()

    def test_invalid_guild_id_does_not_reveal_token(self):
        with patch.dict(os.environ, {"DISCORD_TOKEN": "test-secret-do-not-print", "DEV_GUILD_ID": "invalid"}, clear=True), patch("espresso.__main__.load_environment"), patch("sys.argv", ["espresso"]):
            with self.assertRaises(SystemExit) as caught:
                main()
            self.assertNotIn("test-secret", str(caught.exception))
            self.assertIn("DEV_GUILD_ID", str(caught.exception))
