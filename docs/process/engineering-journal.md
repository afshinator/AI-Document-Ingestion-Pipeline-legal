# Journal

### Purpose

Record what I worked on, observations, open questions



Append-only. Events, not conclusions. One entry per thing worth remembering later, written in a few minutes and never polished. Newest entries at the bottom. Schema is in `../README.md`. Prose is not hard-wrapped: one paragraph per line.

---

<!--
## YYYY-MM-DD Session NNN — <what you set out to do>

**Did.**

**Happened.** What actually occurred, with numbers where they exist.

**Surprised by.** The thing you did not predict, or `Nothing yet`.

Refs: ADR-NNN, EXP-NNN
-->

## 2026-10-25 Session 001 — Dataset investigation setup

**Goal:** Establish an inspectable, reproducible CUAD dataset environment.

**Starting state:** GitHub repository created; CUAD v1 downloaded and extracted.

**Planned work:**
- Verify extracted dataset contents.
- Examine primary annotation formats.
- Trace one annotation to its source contract.

**Observations:** Pending.

**Problems / questions:** Pending.

**Next action:** Inspect the official dataset documentation and file structure.

---

## 2026-10-25 Session 002 — Dataset validator: three false failure classes traced to the validator, two real defects found

**Goal:** Run `scripts/inspect_dataset.py` (v01) against `data/raw/CUAD_v1` and establish whether the dataset is complete and internally consistent.

**Did:**
- Ran v01. Result: `FAIL (6 errors, 1 warning)`, exit 1.
- Investigated every error instead of trusting the count.
- Rewrote the validator as `scripts/inspect_dataset-02.py`, preserving v01 as `inspect_dataset-01.py`, with each change annotated in-code.
- Added `tests/test_canonical_key.py` (11 cases) pinning the filename-matching contract.
- Re-ran v02 against the full directory.

**Happened:**
- v01's PDF count was wrong: it reported 199 of 510. Cause: `rglob("*.pdf")` is case-sensitive on POSIX and 311 files are named `.PDF`. This single bug produced one false `UNEXPECTED_FILE_COUNT` and 312 false `ID_MISSING` errors.
- v01 rejected `CUAD_v1.json`. Cause: the file is a bare list of 510 `{title, paragraphs}` objects, not SQuAD v1's `{"data": [...]}` wrapper. Nothing was wrong with the file.
- v01's CSV-to-filename matching used `Path.stem.casefold()`, so 13 rows "failed" because the CSV keeps characters the filename sanitizer replaced with `_` (`MACY'S` vs `MACY_S`, `A&R` vs `A_R`), plus one cell carrying a stray quote after the extension (`... AGREEMENT.PDF'`).
- Fixing that took two attempts. My first `canonical_key()` stripped the extension *before* removing trailing noise, so `.PDF'` keyed as `agreementpdf` instead of `agreement` — the exact case it was written for. Caught in review, not by the run, because only one CSV row carried the stray quote. Order is now: strip noise, strip extension, strip noise, reduce to alphanumerics.
- v02 result: `FAIL (2 errors, 4 warnings)`. 510 TXT and 510 PDF discovered, JSON accepted: 510 documents, 20,910 questions, 13,822 answer objects.
- Two real defects remain, and neither is validator-induced: one CSV row names a PDF that does not exist on disk while a disk PDF exists that no row names (the row omits `_Option Agreement`), and one of ~13k answer objects has `{"text": [], "answer_start": 466}` instead of a string at `data[316].paragraphs[0].qas[1]`.
- pypdf emitted two `Xref table not zero-indexed` notices on stderr for otherwise-parseable files. Left as `strict=True`; not changed preemptively.

**Surprised by:**
- The validator was wrong about more than the dataset was. 6 errors reduced to 2 once the matching rules reflected how CUAD actually spells filenames, which means the original error count carried no information about data quality.
- Both real defects are single-row/single-label anomalies that a summary count hides. A status of `FAIL` from one mismatched contract reads the same as a broken release.
- One defect can surface twice: the same Harpoon row is reported as both `ID_MISSING` (CSV side) and `UNREFERENCED_FILE` (disk side), which double-counts.

**Decision pending:** whether to add an explicit single-entry override mapping that CSV identifier to the disk filename (dataset becomes joinable 510/510, discrepancy recorded as a warning) or to hold `FAIL` until the upstream name is resolved.

**Next action:** Decide the Harpoon override question, then freeze a dataset snapshot hash before building extraction.

---

## 2026-10-25 Session 003 — Harpoon identity verified; anomaly policy implemented

**Goal:** Decide how to handle the one real filename discrepancy without weakening the matching rules.

**Did:**
- Verified, before writing any code, that the CSV row and the suffixed PDF are the same contract.
- Implemented `CONTRACT_ID_OVERRIDES` with one entry, plus `resolve_overrides()` enforcing five acceptance rules.
- Added `tests/test_source_anomalies.py` (9 cases) covering override acceptance/rejection and the malformed-answer policy.
- Made the report repo-relative so it can be committed, and split `failed_checks` from `source_discrepancies` in the summary.
- Re-ran: `PASS_WITH_WARNINGS (0 errors, 5 warnings)`, exit 0, 1 source discrepancy.

**Happened:** identity evidence, in the order it was gathered rather than the order that reads well:
- The CSV row's own `Document Name-Answer` is `DEVELOPMENT AND OPTION AGREEMENT`, which is what the `_Option Agreement` suffix encodes.
- The TXT named exactly as the CSV identifier and the suffixed PDF are the same document: identical first 300 characters, 248,383 vs 248,527 characters, `difflib` ratio 0.9655 over the first 60k characters, both containing `EXHIBIT 10.18`, `November 20, 2019`, `AbbVie Biotechnology Ltd`, `Harpoon Therapeutics`; the PDF text contains the title `DEVELOPMENT AND OPTION AGREEMENT`.
- 59 of the row's 62 recorded evidence spans are findable in both files after alphanumeric normalization — the *same* 59 in each.
- Only one CSV row references Harpoon, and no other disk file canonicalizes to the suffixed PDF.
- Result: CSV `PDF matches: {'exact': 501, 'explicit_override': 1, 'punct_mismatch': 8}`; `TXT matches: {'exact': 499, 'punct_mismatch': 11}`.

**Surprised by:**
- Literal evidence-span matching is not a reliable identity test on the PDF side: only 26 of 62 spans matched strictly because PDF text extraction hyphenates and reflows. Normalizing to alphanumerics put it at 59, equal to the TXT. Any future check that compares CSV evidence to PDF text must normalize first, or it will produce false negatives at scale.
- 3 of the 62 spans in that row were not found in either source file after alphanumeric normalization: the two `Know-How` definitions and the `Insurance` limits clause. **Status: unresolved, not a proven defect.** Absence under one normalization over two renditions does not establish that the annotation is wrong. Candidate explanations, none yet tested: (a) text extraction dropped or reflowed the passage, (b) the CSV evidence was taken from a different source version of the contract, (c) the span is genuinely absent or misattributed. Recorded as an open question; do not count it as an annotation-integrity finding until one of those is ruled out.
- `PASS_WITH_WARNINGS` arrived without touching a single matching rule. Every earlier failure was either a rule defect or an explicitly recorded exception.

**Decision:** ADR-002 — verified explicit override, discrepancy preserved. Not renaming source files, not editing the CSV, no similarity fallback.

**Next action:** Freeze a dataset snapshot identity, then trace one annotation from CSV to source text to confirm the evidence-span finding at the annotation level rather than the whole-row level. Do not restate the 3 unmatched spans as an annotation defect until the extraction-vs-version question is settled.

Refs: ADR-002
