#!/usr/bin/env python3
"""Tests for the two source-side anomalies and their handling policy.

WHAT: Pins (a) the acceptance rules for CONTRACT_ID_OVERRIDES and (b) the rule
that one ill-typed JSON answer is recorded, not fatal.

WHY: An override is a hand-written claim that two different names mean one
contract, so the failure mode that matters is an override applied when it should
not have been. And the JSON validator must not let one annotation defect hide the
counts for the other 13,821 answers.

RUN: uv run python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "inspect_dataset-02.py"
_spec = importlib.util.spec_from_file_location("inspect_dataset_02_anomalies", SCRIPT)
assert _spec and _spec.loader
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

CSV_ID = "HarpoonTherapeuticsInc_20200312_10-K_EX-10.18_12051356_EX-10.18_Development Agreement.PDF"
PDF_FILE = "HarpoonTherapeuticsInc_20200312_10-K_EX-10.18_12051356_EX-10.18_Development Agreement_Option Agreement.pdf"


def index_of(*names: str) -> dict[str, list[pathlib.Path]]:
    index: dict[str, list[pathlib.Path]] = {}
    for name in names:
        index.setdefault(mod.canonical_key(name), []).append(pathlib.Path("/disk") / name)
    return index


class OverrideAcceptanceTests(unittest.TestCase):
    def test_shipped_override_applies_to_the_real_names(self):
        applied, rejected = mod.resolve_overrides([CSV_ID], index_of(PDF_FILE))
        self.assertEqual(rejected, [])
        self.assertEqual(list(applied), [CSV_ID])
        self.assertEqual(applied[CSV_ID].name, PDF_FILE)

    def test_rejected_when_target_absent_from_disk(self):
        applied, rejected = mod.resolve_overrides([CSV_ID], index_of("Something Else.pdf"))
        self.assertEqual(applied, {})
        self.assertEqual(rejected[0]["rule"], "target resolves to 0 files, expected 1")

    def test_rejected_when_target_already_claimed_by_another_identifier(self):
        applied, rejected = mod.resolve_overrides([CSV_ID, PDF_FILE], index_of(PDF_FILE))
        self.assertEqual(applied, {})
        self.assertIn("already claimed by CSV identifier", rejected[0]["rule"])

    def test_rejected_when_identifier_absent_from_csv(self):
        with mock.patch.object(mod, "CONTRACT_ID_OVERRIDES", {CSV_ID: dict(mod.CONTRACT_ID_OVERRIDES[CSV_ID])}):
            applied, rejected = mod.resolve_overrides(["Unrelated.pdf"], index_of(PDF_FILE))
        self.assertEqual(applied, {})
        self.assertEqual(rejected[0]["rule"], "identifier not present in CSV")

    def test_rejected_without_documented_reason_or_evidence(self):
        spec = {"file": PDF_FILE, "reason": "", "evidence": ""}
        with mock.patch.object(mod, "CONTRACT_ID_OVERRIDES", {CSV_ID: spec}):
            applied, rejected = mod.resolve_overrides([CSV_ID], index_of(PDF_FILE))
        self.assertEqual(applied, {})
        self.assertEqual(rejected[0]["rule"], "missing documented reason or evidence")

    def test_rejected_when_two_overrides_share_one_target(self):
        second = "Another Contract.PDF"
        both = {
            CSV_ID: {"file": PDF_FILE, "reason": "r", "evidence": "e"},
            second: {"file": PDF_FILE, "reason": "r", "evidence": "e"},
        }
        with mock.patch.object(mod, "CONTRACT_ID_OVERRIDES", both):
            applied, rejected = mod.resolve_overrides([CSV_ID, second], index_of(PDF_FILE))
        self.assertEqual(list(applied), [CSV_ID])
        self.assertEqual(rejected[0]["rule"], "target already assigned by another override")


class MalformedAnswerTests(unittest.TestCase):
    def _report(self) -> dict:
        return {"inventory": {}, "csv": None, "json": None, "issues": []}

    def _write(self, directory: str, payload: object) -> pathlib.Path:
        root = pathlib.Path(directory)
        (root / "CUAD_v1.json").write_text(json.dumps(payload), encoding="utf-8")
        return root

    def test_anomalous_answer_is_recorded_and_validation_continues(self):
        payload = [{
            "title": "T",
            "paragraphs": [{
                "context": "c",
                "qas": [{
                    "id": "q", "question": "q?", "is_impossible": False,
                    "answers": [
                        {"text": [], "answer_start": 466},
                        {"text": "Agent", "answer_start": 476},
                    ],
                }],
            }],
        }]
        with tempfile.TemporaryDirectory() as tmp:
            report = self._report()
            mod.check_json(self._write(tmp, payload), report)

        self.assertEqual(report["json"]["shape"], "list")
        self.assertEqual(report["json"]["questions"], 1)
        self.assertEqual(report["json"]["answer_objects"], 1, "good answer still counted")
        self.assertEqual(report["json"]["malformed_answers"]["count"], 1)
        location = report["json"]["malformed_answers"]["locations"][0]
        self.assertEqual((location["document"], location["paragraph"], location["question"]), (0, 0, 0))
        self.assertEqual(location["text_type"], "list")

        codes = [issue["code"] for issue in report["issues"]]
        self.assertNotIn("INVALID_JSON", codes)
        self.assertIn("MALFORMED_ANSWER", codes)
        # JSON_DOCUMENT_COUNT also fires here because the fixture has one document;
        # what matters is that neither issue is an error-level abort.
        self.assertEqual({issue["level"] for issue in report["issues"]}, {"warning"})

    def test_squad_data_wrapper_still_accepted(self):
        payload = {"version": "v2.0", "data": [{"title": "T", "paragraphs": [{"context": "c", "qas": []}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            report = self._report()
            mod.check_json(self._write(tmp, payload), report)
        self.assertEqual(report["json"]["shape"], "data_wrapper")
        self.assertEqual(report["json"]["version"], "v2.0")
        self.assertEqual(report["json"]["documents"], 1)

    def test_structural_defect_still_raises(self):
        payload = [{"title": "T", "paragraphs": "not-a-list"}]
        with tempfile.TemporaryDirectory() as tmp:
            report = self._report()
            mod.check_json(self._write(tmp, payload), report)
        self.assertIn("INVALID_JSON", [issue["code"] for issue in report["issues"]])
        self.assertEqual(report["json"], None)


if __name__ == "__main__":
    unittest.main()
