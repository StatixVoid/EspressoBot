from dataclasses import asdict
import unittest

from espresso.rules import Settings, content_decision, content_violation


class HateFilterTests(unittest.TestCase):
    def config(self, category, level, **changes):
        settings = Settings(block_invites=False)
        settings.hate_filters[category].update(enabled=True, level=level, **changes)
        return settings

    def test_categories_are_opt_in_and_neutral_mentions_are_not_blocked(self):
        for text in ("women are inferior", "no trans people allowed", "no black people allowed"):
            self.assertIsNone(content_violation(text, Settings()))
        for category in ("racism", "misogyny", "trans_hate"):
            settings = self.config(category, 5)
            for text in ("I am trans", "Black people deserve equal rights", "women in technology", "Scunthorpe"):
                self.assertIsNone(content_violation(text, settings))

    def test_levels_add_coverage_in_each_category(self):
        examples = {"racism":("kike", "raghead", "no black people allowed", "k.i.k.e", "black people are all criminals"),
                    "misogyny":("femoid", "bitch", "women belong in the kitchen", "b!tch", "women are stupid"),
                    "trans_hate":("tranny", "troon", "no trans people allowed", "t.r.a.n.n.y", "trans people are all predators")}
        for category, messages in examples.items():
            for minimum, text in enumerate(messages, 1):
                for level in range(1, 6):
                    with self.subTest(category=category, text=text, level=level):
                        match = content_decision(text, self.config(category, level))
                        self.assertEqual(match is not None, level >= minimum)

    def test_exact_exceptions_skip_only_the_matching_occurrence(self):
        settings = self.config("misogyny",2,allowed_terms=["bitch"])
        self.assertIsNone(content_decision("bitch",settings))
        self.assertIsNotNone(content_decision("bitch and whore",settings))
        settings.bad_words = ["bitch"]
        self.assertEqual(content_violation("bitch",settings),"Blocked word or phrase")

    def test_extra_phrases_and_independent_actions(self):
        settings = self.config("misogyny",1,extra_terms=["test phrase"],action="delete")
        match = content_decision("TEST PHRASE",settings)
        self.assertEqual(match.action,"delete")
        self.assertEqual(match.category,"misogyny")
        settings.hate_filters["racism"].update(enabled=True,level=3,action="timeout")
        self.assertEqual(content_decision("test phrase; no black people allowed",settings).action,"timeout")

    def test_category_delete_only_cannot_suppress_a_separate_warning_rule(self):
        settings = self.config("misogyny",3,action="delete")
        settings.block_invites = True
        match = content_decision("women belong in the kitchen discord.gg/test",settings)
        self.assertEqual(match.action,"warn")
        self.assertEqual(match.reason,"Discord invite")

    def test_malformed_filters_are_rejected(self):
        for key,value in (("level",True),("level",0),("level",6),("enabled","false"),("action",[]),("extra_terms",[1])):
            settings = asdict(Settings())
            settings["hate_filters"]["racism"][key] = value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                Settings(**settings)
