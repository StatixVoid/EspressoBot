import unittest

from espresso.rules import Settings, SpamTracker, content_violation


class RuleTests(unittest.TestCase):
    def test_literal_word_boundaries_and_phrases(self):
        settings = Settings(block_invites=False, bad_words=["ass", "go away", "a+b"])
        self.assertIsNone(content_violation("class assignment", settings))
        for text in ("ASS!", "please go away.", "a+b", "ａｓｓ", "a\u200bss"):
            with self.subTest(text=text):
                self.assertEqual(content_violation(text, settings), "Blocked word or phrase")
        self.assertIsNone(content_violation("aaab", settings))

    def test_invites_and_lookalike_hosts(self):
        settings = Settings()
        for text in ("discord.gg/test", "https://discord.com/invite/abc",
                     "WWW.DISCORDAPP.COM/invite/123", "https://discord.gg/abc?x=1"):
            self.assertEqual(content_violation(text, settings), "Discord invite")
        for text in ("mydiscord.gg/test", "notdiscord.com/invite/test", "discord.gg.evil.com/test"):
            self.assertIsNone(content_violation(text, settings))

    def test_link_filter_and_toggles(self):
        settings = Settings(block_links=True, block_invites=False)
        for text in ("https://example.com/x", "http://127.0.0.1", "www.example.org", "example.com/path"):
            self.assertEqual(content_violation(text, settings), "Link")
        self.assertIsNone(content_violation("hello world", settings))
        settings.block_links = False
        self.assertIsNone(content_violation("discord.gg/test https://example.com", settings))

    def test_spam_window_and_guild_isolation(self):
        tracker = SpamTracker()
        self.assertFalse(tracker.hit(1, 10, 0, 3, 5))
        self.assertFalse(tracker.hit(1, 10, 1, 3, 5))
        self.assertFalse(tracker.hit(2, 10, 2, 3, 5))
        self.assertFalse(tracker.hit(1, 11, 2, 3, 5))
        self.assertTrue(tracker.hit(1, 10, 2, 3, 5))
        self.assertFalse(tracker.hit(1, 10, 8, 3, 5))

    def test_spam_memory_bound_and_reset(self):
        tracker = SpamTracker(capacity=2)
        for user in range(100):
            tracker.hit(1, user, user, 2, 5)
        self.assertEqual(len(tracker.buckets), 2)
        tracker.reset_guild(1)
        self.assertFalse(tracker.buckets)

    def test_settings_reject_invalid_values(self):
        for changes in ({"spam_limit": 0}, {"timeout_minutes": 40321}, {"enabled": "false"},
                        {"warn_threshold": True}, {"bad_words": ["\u200b"]},
                        {"exempt_roles": [-1]}, {"log_channel": "123"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Settings(**changes)
