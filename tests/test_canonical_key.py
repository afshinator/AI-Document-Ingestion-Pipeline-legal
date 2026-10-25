#!/usr/bin/env python3
"""Unit tests for scripts/inspect_dataset-02.py name matching.

WHAT: Pins the canonical_key() contract and the collision detectors that the CSV
and disk indexes depend on.

WHY: The v01 validator's failures were all name-matching defects, and the first
v02 attempt still got one of them wrong (.PDF' keyed as "agreementpdf") without
any run exposing it. These cases are cheap and the failures are silent.

RUN: uv run python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "inspect_dataset-02.py"
_spec = importlib.util.spec_from_file_location("inspect_dataset_02", SCRIPT)
assert _spec and _spec.loader
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

canonical_key = mod.canonical_key
find_canonical_collisions = mod.find_canonical_collisions


class CanonicalKeyTests(unittest.TestCase):
    def test_extension_case_is_ignored(self):
        self.assertEqual(canonical_key("Foo AGREEMENT.PDF"), "fooagreement")
        self.assertEqual(canonical_key("Foo AGREEMENT.pdf"), "fooagreement")

    def test_stray_quote_after_extension(self):
        # KALLOINC row in master_clauses.csv ends ".PDF'".
        self.assertEqual(canonical_key("KALLOINC AGREEMENT.PDF"), canonical_key("KALLOINC AGREEMENT.PDF'"))

    def test_trailing_space_before_extension(self):
        self.assertEqual(canonical_key("MARKETING AGREEMENT .txt"), "marketingagreement")
        self.assertEqual(canonical_key("MARKETING AGREEMENT .txt"), canonical_key("MARKETING AGREEMENT.txt"))

    def test_capitalization_difference(self):
        self.assertEqual(canonical_key("MACY_S,INC_05_11_2020-EX-99.4-JOINT FILING AGREEMENT.TXT"),
                         canonical_key("Macy_s,inc_05_11_2020-ex-99.4-joint filing agreement.txt"))

    def test_punctuation_substitution(self):
        self.assertEqual(canonical_key("SECOND A&R AGREEMENT.PDF"), canonical_key("SECOND A_R AGREEMENT.pdf"))
        self.assertEqual(canonical_key("MACY'S,INC.TXT"), canonical_key("MACY_S,INC.txt"))
        self.assertEqual(canonical_key("Acme (Part II).PDF"), canonical_key("Acme _Part II_.PDF"))

    def test_dict_content_mismatch_stays_distinct(self):
        # Real defect on disk: the CSV row omits "_Option Agreement".
        self.assertNotEqual(canonical_key("Harpoon Development Agreement.PDF"),
                            canonical_key("Harpoon Development Agreement_Option Agreement.PDF"))

    def test_extension_not_treated_as_suffix_noise(self):
        self.assertNotEqual(canonical_key("A.pdf"), canonical_key("A.txt.bak"))

    def test_empty_and_noise_only(self):
        self.assertEqual(canonical_key(""), "")
        self.assertEqual(canonical_key("  '__ .PDF "), "")


class CollisionTests(unittest.TestCase):
    def test_canonical_collision_detected(self):
        names = ["ABC-DEF.pdf", "ABC_DEF.pdf"]
        found = find_canonical_collisions(names)
        self.assertEqual(list(found), ["abcdef"])
        self.assertEqual(sorted(found["abcdef"]), sorted(names))

    def test_unique_names_report_no_collision(self):
        self.assertEqual(find_canonical_collisions(["A.pdf", "B.pdf", "C.TXT"]), {})

    def test_empty_identifiers_ignored(self):
        self.assertEqual(find_canonical_collisions(["", "", "A.pdf"]), {})


if __name__ == "__main__":
    unittest.main()
