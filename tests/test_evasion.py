import json
from pathlib import Path
import unittest

from espresso.evasion import evasion_view, phrase_evasion
from espresso.hate_filters import TERMS, PATTERNS, text_key
from espresso.rules import Settings, content_decision
from espresso.simulation import simulate


EXAMPLES = Path(__file__).parents[1]/"espresso/public/filter-examples.json"


def settings_for(category="trans_hate",level=5,**changes):
    settings=Settings(enabled=True,block_invites=False)
    settings.hate_filters[category].update(enabled=True,level=level,**changes)
    return settings


class EvasionTests(unittest.TestCase):
    def test_every_displayed_example_matches_its_category_and_inherited_levels(self):
        data=json.loads(EXAMPLES.read_text(encoding="utf-8"))
        self.assertEqual(set(data["categories"]),set(TERMS))
        for category,levels in data["categories"].items():
            self.assertEqual(set(levels),{"1","2","3","4","5"})
            for minimum,group in levels.items():
                for example in group["examples"]:
                    for level in range(int(minimum),6):
                        with self.subTest(category=category,minimum=minimum,label=example["label"],level=level):
                            decision=content_decision(example["text"],settings_for(category,level))
                            self.assertIsNotNone(decision)
                            self.assertEqual(decision.category,category)
                            self.assertEqual(decision.unicode_evasion,example["unicode_evasion"])
                            self.assertEqual(decision.action,"timeout" if example["unicode_evasion"] else "warn")

    def test_evasion_levels_preserve_lower_coverage_boundaries(self):
        for category,text in (("racism","k . 1 . k . 3"),("misogyny","wоmеn bеlоng in thе kitсhеn"),("trans_hate","t.r.4.n.n.y")):
            for level in range(1,6):
                with self.subTest(category=category,level=level):
                    self.assertEqual(content_decision(text,settings_for(category,level)) is not None,level>=4)
        self.assertIsNone(content_decision("transpeopleareallpredators",settings_for(level=4)))
        self.assertIsNotNone(content_decision("transpeopleareallpredators",settings_for(level=5)))

    def test_unicode_is_not_itself_a_violation(self):
        examples=("I’m trans 🏳️‍⚧️","José supports trans people 💜","women in technology 👩🏽‍💻",
                  "Black people deserve equal rights ✊🏿","Здравствуйте","Καλημέρα","你好","مرحباً",
                  "Scunthorpe has a good community.","This classification is useful.")
        for category in TERMS:
            for text in examples:
                with self.subTest(category=category,text=text):self.assertIsNone(content_decision(text,settings_for(category)))

    def test_unicode_outside_the_match_does_not_escalate_plain_abuse(self):
        for text in ("José said: tranny", "tranny 🏳️‍⚧️", "💜 tranny", "trans people don’t belong here"):
            decision=content_decision(text,settings_for(action="delete"))
            self.assertIsNotNone(decision)
            self.assertFalse(decision.unicode_evasion)
            self.assertEqual(decision.action,"delete")

    def test_unicode_disguises_override_category_action_without_widening_to_benign_unicode(self):
        for text in ("τrаnոу","t\u200branny","tranný","ｔｒａｎｎｙ","t\u034franny","t🧩r🧩a🧩n🧩n🧩y","no trans 🧩 people allowed"):
            with self.subTest(text=text):
                decision=content_decision(text,settings_for(action="delete"))
                self.assertIsNotNone(decision)
                self.assertTrue(decision.unicode_evasion)
                self.assertEqual(decision.action,"timeout")
                self.assertIn("Unicode evasion",decision.reason)

    def test_ascii_evasions_keep_the_selected_action(self):
        for text in ("t.r.4.n.n.y","tr"+"a"*200+"nny","t_r_a_n_n_y","t**r**a**n**n**y"):
            with self.subTest(text=text):
                decision=content_decision(text,settings_for(action="delete"))
                self.assertIsNotNone(decision)
                self.assertFalse(decision.unicode_evasion)
                self.assertEqual(decision.action,"delete")

    def test_normalization_based_unicode_matches_at_lower_levels_escalate_too(self):
        for text in ("ｔｒａｎｎｙ","tr\u200banny"):
            decision=content_decision(text,settings_for(level=1,action="delete"))
            self.assertTrue(decision.unicode_evasion);self.assertEqual(decision.action,"timeout")

    def test_exceptions_remain_exact_and_do_not_let_a_plain_allowed_term_whitelist_variants(self):
        settings=settings_for(allowed_terms=["tranny"])
        self.assertIsNone(content_decision("(tranny!)",settings))
        for text in ("t.r.a.n.n.y","tranný","τrаnոу","tr"+"a"*200+"nny"):
            with self.subTest(text=text):self.assertIsNotNone(content_decision(text,settings))
        settings.hate_filters["trans_hate"]["allowed_terms"]=["τrаnոу"]
        self.assertIsNone(content_decision("τrаnոу",settings))
        self.assertIsNotNone(content_decision("τrаnոу and troon",settings))
        settings.bad_words=["τrаnոу"]
        self.assertEqual(content_decision("τrаnոу",settings).reason,"Blocked word or phrase")

    def test_extra_terms_follow_evasion_level_without_treating_regex_as_syntax(self):
        for level in (3,4,5):
            settings=settings_for(level=level,extra_terms=["test phrase","a+b"])
            self.assertEqual(content_decision("t.e.s.t p.h.r.a.s.e",settings) is not None,level>=4)
            self.assertIsNone(content_decision("ab",settings))
            self.assertIsNotNone(content_decision("a+b",settings))
        settings=settings_for(extra_terms=["test phrase"],action="delete")
        self.assertTrue(content_decision("tеst phrаsе",settings).unicode_evasion)

    def test_a_literal_unicode_extra_term_is_not_itself_evasion(self):
        settings=settings_for(extra_terms=["café"],action="delete")
        for text in ("café","cafe\u0301"):
            decision=content_decision(text,settings)
            self.assertIsNotNone(decision);self.assertEqual(decision.action,"delete");self.assertFalse(decision.unicode_evasion)
        for text in ("c\u200bafé","cаfé"):
            self.assertTrue(content_decision(text,settings).unicode_evasion)

    def test_stronger_unicode_match_wins_over_other_timeout_rules_and_plain_matches(self):
        settings=settings_for(action="delete")
        settings.hate_filters["misogyny"].update(enabled=True,level=5,action="timeout")
        for text in ("women are stupid; τrаnոу","tranny then τrаnոу"):
            self.assertTrue(content_decision(text,settings).unicode_evasion)

    def test_simulator_shows_timeout_despite_warning_cooldown_and_respects_observation(self):
        settings=settings_for(action="delete")
        result=simulate("τrаnոу",settings,{"warning_cooldown":True,"active_warnings":2})
        self.assertTrue(result["timeout_would_apply"]);self.assertEqual(result["warnings_after"],2)
        self.assertIn("independent of warning cooldown",result["note"])
        blocked=simulate("τrаnոу",settings,{"warning_cooldown":True},timeout_error="Role hierarchy blocks this timeout.")
        self.assertFalse(blocked["timeout_would_apply"]);self.assertEqual(blocked["effective_action"],"delete")
        settings.observation_mode=True
        self.assertEqual(simulate("τrаnոу",settings,{})["effective_action"],"observe")

    def test_source_spans_cover_removed_marks_and_long_runs(self):
        original="t\u0336\u200br"+"a"*100+"nny"
        view=evasion_view(original)
        self.assertEqual(view.spans[0],(0,3))
        self.assertEqual(view.spans[-1][1],len(original))
        self.assertLess(len(view.text),len(original))

    def test_all_starter_phrase_syntax_is_supported_and_unknown_syntax_is_rejected(self):
        for levels in PATTERNS.values():
            for patterns in levels.values():
                for pattern in patterns:self.assertTrue(phrase_evasion(pattern))
        with self.assertRaises(ValueError):phrase_evasion("[a-z]+")
