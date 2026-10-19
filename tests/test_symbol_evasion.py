import unittest

from espresso.rules import Settings, content_decision
from espresso.evasion import evasion_view, masked_evasion
from espresso.hate_filters import TERMS
from test_evasion import settings_for


class SymbolEvasionTests(unittest.TestCase):
    def test_reported_bracket_bypass_and_related_spellings_match_at_evasion_levels(self):
        variants=("Tr(an)y","tr[an]y","tr{an}y","tr<an>y","tr(a)ny","tr(an)ys","tr@ny","tr4ny","trany","tr(a)nies")
        for text in variants:
            for level in range(1,6):
                with self.subTest(text=text,level=level):
                    match=content_decision(text,settings_for(level=level))
                    self.assertEqual(match is not None,level>=4)
                    if match:self.assertEqual(match.category,"trans_hate")

    def test_ascii_letter_shapes_match_across_categories(self):
        variants={
            "racism":("rag|-|ead","|<i|<e"),
            "misogyny":("fem()id","fem[]id","fem{}id","fem<>id","|3itch","s1|_|+","w()men be|()ng in the kit(hen"),
            "trans_hate":("tra|\\||\\|y","tr@|\\|y","t|2anny","tra/\\/y"),
        }
        for category,texts in variants.items():
            for text in texts:
                with self.subTest(category=category,text=text):
                    self.assertIsNone(content_decision(text,settings_for(category,3)))
                    for level in (4,5):
                        match=content_decision(text,settings_for(category,level))
                        self.assertIsNotNone(match);self.assertEqual(match.category,category)

    def test_ascii_evasions_keep_the_configured_action_and_unicode_evasions_escalate(self):
        for action in ("delete","warn","timeout"):
            settings=settings_for(action=action)
            for text in ("Tr(an)y","tr@|\\|y"):
                decision=content_decision(text,settings)
                self.assertEqual(decision.action,action);self.assertFalse(decision.unicode_evasion)
            decision=content_decision("tr(аո)y",settings)
            self.assertEqual(decision.action,"timeout");self.assertTrue(decision.unicode_evasion)

    def test_shortened_letters_do_not_turn_into_general_fuzzy_matching(self):
        settings=Settings(block_invites=False)
        for config in settings.hate_filters.values():config.update(enabled=True,level=5)
        for text in ("Tron","I watched TRON yesterday.","Niger","Niger's history","Ｎｉｇｅｒ","trans people deserve respect", "a tranquil place", "Tr(an)quil", "xTr(an)y", "Tr(an)yard", "brackets () [] {} and mathematics |x|", "femoral", "a normal function()"):
            with self.subTest(text=text):self.assertIsNone(content_decision(text,settings))

    def test_symbol_shapes_apply_to_extra_terms_without_reinterpreting_literal_punctuation(self):
        settings=settings_for(extra_terms=["bonus","c++"],action="delete")
        self.assertIsNotNone(content_decision("b()|\\|us",settings))
        self.assertIsNotNone(content_decision("c++",settings))
        for text in ("c","c+","cpp"):
            with self.subTest(text=text):self.assertIsNone(content_decision(text,settings))

    def test_exact_exceptions_retain_the_original_symbol_span(self):
        settings=settings_for(allowed_terms=["Tr(an)y"])
        self.assertIsNone(content_decision("`Tr(an)y`",settings))
        self.assertIsNotNone(content_decision("tr[an]y",settings))
        self.assertIsNotNone(content_decision("Tr(an)y and troon",settings))
        settings.hate_filters["trans_hate"]["allowed_terms"]=["trany"]
        self.assertIsNone(content_decision("trany",settings))
        self.assertIsNotNone(content_decision("Tr(an)y",settings))
        settings=settings_for("misogyny",allowed_terms=["fem()id"])
        self.assertIsNone(content_decision("fem()id",settings))
        self.assertIsNotNone(content_decision("fem[]id",settings))

    def test_disabling_the_category_still_disables_matching(self):
        settings=settings_for();settings.hate_filters["trans_hate"]["enabled"]=False
        self.assertIsNone(content_decision("Tr(an)y",settings))

    def test_symbols_can_mask_a_bounded_number_of_letters(self):
        variants={
            "racism":("k*ke","r#ghe%d","n**ga"),
            "misogyny":("c_nt","b*tch","b**ch","f*m*id","fem***id"),
            "trans_hate":("tr*nny","tr#nny","tr%nny","tr^nny","tr?nny","tr_nny","tr**ny","trann*","*ranny","tr**y"),
        }
        for category,texts in variants.items():
            for text in texts:
                for level in range(1,6):
                    with self.subTest(category=category,text=text,level=level):
                        decision=content_decision(text,settings_for(category,level,action="delete"))
                        self.assertEqual(decision is not None,level>=4)
                        if decision:
                            self.assertEqual(decision.category,category)
                            self.assertEqual(decision.action,"delete")
                            self.assertFalse(decision.unicode_evasion)

    def test_unicode_symbol_shapes_and_emoji_masks_escalate_only_inside_abuse(self):
        variants={
            "racism":("®agh€ad","¢hink"),
            "misogyny":("fem●id","f€m()id","w()m€n be|_()ng in -|-he kit¢hen"),
            "trans_hate":("tr∆nn¥","†r∧∩∩¥","tr💜nny"),
        }
        for category,texts in variants.items():
            for text in texts:
                with self.subTest(category=category,text=text):
                    decision=content_decision(text,settings_for(category,action="delete"))
                    self.assertIsNotNone(decision)
                    self.assertTrue(decision.unicode_evasion)
                    self.assertEqual(decision.action,"timeout")
        for text in ("tr*nny ☕","💜 tr**nny","tranny 💜","tranny💜"):
            with self.subTest(text=text):
                decision=content_decision(text,settings_for(action="delete"))
                self.assertFalse(decision.unicode_evasion)
                self.assertEqual(decision.action,"delete")

    def test_more_ascii_shapes_and_long_mixed_separator_runs(self):
        for text in (r"tr/\nny",r"trann\|/",r"-|-ranny",(".!?"*20).join("tranny")):
            with self.subTest(text=text):
                decision=content_decision(text,settings_for(action="warn"))
                self.assertIsNotNone(decision)
                self.assertFalse(decision.unicode_evasion)
        decision=content_decision(("💜!?"*20).join("tranny"),settings_for(action="delete"))
        self.assertTrue(decision.unicode_evasion)

    def test_masks_preserve_boundaries_exact_exceptions_and_plural_source_spans(self):
        settings=settings_for(allowed_terms=["tr**nny"])
        self.assertIsNone(content_decision("`tr**nny`",settings))
        self.assertIsNotNone(content_decision("tr#nny",settings))
        self.assertIsNotNone(content_decision("tr**nny and tr?nny",settings))
        settings.hate_filters["trans_hate"]["allowed_terms"]=["tr*nny"]
        self.assertIsNotNone(content_decision("tr*nnys",settings))
        settings.hate_filters["trans_hate"]["allowed_terms"]=["tr*nnys"]
        self.assertIsNone(content_decision("tr*nnys",settings))
        for text in ("extr*nny","tr*nnyard","tr*nny_road"):
            with self.subTest(text=text):self.assertIsNone(content_decision(text,settings_for()))
        for text in ("x!?ranny","trann!?x","t!?r!?a!?n!?n!?x", "t"+"!?"*1000+"ranny"):
            with self.subTest(text=text):self.assertIsNone(content_decision(text,settings_for()))
        original="tr"+"*"*100+"nny"
        view=evasion_view(original)
        match=next(masked_evasion("tranny").finditer(view.text))
        self.assertEqual(view.source(match,original),original)

    def test_masks_do_not_turn_ordinary_symbols_or_short_terms_into_abuse(self):
        settings=Settings(block_invites=False)
        for config in settings.hate_filters.values():config.update(enabled=True,level=5)
        examples=("***","$#@!?","t****y","s**t","t*r*y","my_name","file_name.py", "a[0] += 1; b *= 2",
                  "Math: ∆x × 2; €5 and £10", "I love trans people 💜", "coffee ☕", "Tron", "Niger",
                  "xtr*nny","tr*nnyish","café", "© 2026. All rights reserved.", "Tran's", "Tran’s",
                  "'Ike'", chr(96)+"Ike"+chr(96), "**Ike**", "*itch*", "**Anny**", "*Tran*", "~~itch~~", "||itch||",
                  "(Ike)", "Tran.", "Tran!", "Tran 💜", "Tran💜💜", "trann 🧩")
        for text in examples:
            with self.subTest(text=text):self.assertIsNone(content_decision(text,settings))
        settings=settings_for(extra_terms=["cat","c++","bonus"],action="delete")
        for text in ("c*t","c*+","c+","c","cpp"):
            with self.subTest(text=text):self.assertIsNone(content_decision(text,settings))
        self.assertIsNotNone(content_decision("b*n*s",settings))
        self.assertIsNotNone(content_decision("b()n*s",settings))
        self.assertIsNotNone(content_decision("c++",settings))

    def test_markdown_wrappers_do_not_hide_internal_masked_abuse(self):
        for text in ("**tr*nny**","*tr*nny*","__tr*nny__","||tr*nny||","~~tr*nny~~"):
            with self.subTest(text=text):self.assertIsNotNone(content_decision(text,settings_for()))

    def test_every_starter_slur_supports_internal_single_letter_masks(self):
        for category,levels in TERMS.items():
            settings=settings_for(category,4)
            for terms in levels.values():
                for term in terms:
                    for index,character in enumerate(term):
                        if not character.isalpha() or index in (0,len(term)-1):continue
                        text=term[:index]+"*"+term[index+1:]
                        with self.subTest(category=category,text=text):
                            self.assertIsNotNone(content_decision(text,settings))

    def test_formatted_literal_and_phrase_evasions_preserve_source_exceptions(self):
        for text in ("__tranny__","***tranny***","||tranny||","__no trans people allowed__"):
            with self.subTest(text=text):self.assertIsNotNone(content_decision(text,settings_for()))
        self.assertIsNone(content_decision("__tranny__",settings_for(allowed_terms=["tranny"])))

    def test_long_symbol_heavy_near_misses_do_not_create_matches(self):
        settings=Settings(block_invites=False)
        for config in settings.hate_filters.values():config.update(enabled=True,level=5)
        cases=("!?"*2000,"t"+"!?"*1900+"ranny","t!?r!?a!?n!?n!?x "*250,"|!_*?"*800,"tr"+"💜!?"*1000+"anny")
        for index,text in enumerate(cases):
            with self.subTest(case=index):self.assertIsNone(content_decision(text,settings))
