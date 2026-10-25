# Decisions

### Purpose

Explain consequential choices and alternatives

Append-only. One entry per meaningful choice, including choices about process. Superseded entries stay in place and gain `Status: superseded by ADR-NNN`. Schema is in `../README.md`. Prose is not hard-wrapped: one paragraph per line.

---

## 2026-10-24 ADR-001 — Stable prefixed IDs plus a `Refs:` commit trailer

**Status:** accepted

**Context.** The case study must be supported by actual evidence gathered during development. That only works if the evidence is machine-findable, which rules out prose that refers to "the chunking decision" or "that experiment in week 3."

**Options.**

- Free prose cross-references between documents.
- Per-document local numbering (`D1`, `E3`).
- Globally unique prefixed IDs (`ADR-001`, `EXP-007`, `INC-003`).

**Decision.** Globally unique prefixed IDs, never reused, referenced from git commit messages via a `Refs:` trailer. Local numbering rejected: it collides across documents and with shorthand used in other contexts. Same ID is reused nowhere, so it is greppable from any context.

**Consequences.** Entry headings are fixed as `## YYYY-MM-DD <ID> — <title>` (ISO dates) so a shell one-liner can pull a milestone's evidence. Cost is a namespace to maintain by hand.

---

## 2026-10-25 ADR-002 — Resolve source-name discrepancies with verified explicit overrides, never by relaxing matching

**Status:** accepted

**Context.** One of 510 contracts has a CSV identifier that does not equal the source PDF filename: the row says `..._EX-10.18_Development Agreement.PDF` while the PDF on disk is `..._EX-10.18_Development Agreement_Option Agreement.pdf`. The TXT named exactly as the CSV row exists, so the join fails only for the PDF side. This is a data-quality defect in how the release spells one name, not a validator bug like the earlier three.

**Options.**

- **O1** Explicit, verified, single-entry override mapping CSV identifier to source filename.
- **O2** Leave the inventory at `FAIL` until the name is resolved upstream.
- **O3** Rename the source PDF to match the CSV.
- **O4** Loosen the global matching rules (prefix, substring, or similarity fallback).

**Decision.** O1. O3 and O4 are rejected because both destroy the evidence: O3 mutates the published dataset, and O4 could silently join unrelated contracts while making future real defects unmatchable. O2 is correct until identity is proven, and identity was proven, so it no longer applies.

An override is accepted only if: the identifier exists in the CSV; the target resolves to exactly one file on disk; no other CSV identifier already claims that target; no two overrides share a target; and the entry carries a documented reason and evidence. Any failed rule leaves the mismatch as an error.

Matching after an override is classified `explicit_override`, never `exact`.

**Consequences.** The dataset joins 510/510 and the inventory reaches `PASS_WITH_WARNINGS`, while the discrepancy stays visible in `source_discrepancies` and as a warning. The report counts failed checks separately from underlying discrepancies, because one mismatch is visible from both directions (`ID_MISSING` from the CSV side, `UNREFERENCED_FILE` from the disk side) and two failed checks must not read as two defects. Cost: a hand-maintained map that must be re-verified if the dataset is ever re-downloaded.

Identity evidence for the one shipped entry is recorded in `CONTRACT_ID_OVERRIDES` in `scripts/inspect_dataset-02.py`.

---

## 2026-10-25 ADR-003 — No published archive of the corpus exists; omit `--archive` and record the checksum as unavailable

**Status:** accepted

**Context.** The validator accepts `--archive` to record a SHA-256 for dataset provenance. That only means something if a published archive can be hashed. Searched and found nothing that matches the 510-contract corpus:

- `atticusprojectai.org/cuad` links only to Hugging Face. No archive link on the page.
- `huggingface.co/datasets/theatticusproject/cuad` is 745 exploded files with no zip, and it has no `label_group_xlsx/`, so it is not even the same file set as the checkout.
- `github.com/The-Atticus-Project/cuad` holds one archive, `data.zip`, 18,309,308 bytes, `sha256 f8161d18bea4e9c05e78fa6dda61c19c846fb8087ea969c172753bc2f45b999a`. It contains three SQuAD JSON files (`CUADv1.json`, `test.json`, `train_separate_questions.json`) and no contracts. No releases, no LFS.

**Options.**

- **O1** Omit `--archive`; report `archive_sha256: null` and a `NO_ARCHIVE` warning that cites this ADR.
- **O2** Pass `data.zip` so a checksum appears. Rejected: the report would record a digest of unrelated files next to `dataset: CUAD v1`, which reads as verified provenance and is not.
- **O3** Download the 745 Hugging Face files and repackage them. Rejected: hundreds of requests, and the resulting digest still would not authenticate this tree, whose file set differs.
- **O4** Build a zip from the local tree. Deferred: the digest would only prove the bytes have not changed since the zip was built, not where they came from. Legitimate later as a snapshot freeze, and must be labelled locally built when it happens.

**Decision.** O1. Provenance in the report rests instead on a SHA-256 and byte count per input file (`provenance.inputs`) and on the script's own digest (`tool.sha256`), plus this ADR recording why no archive digest exists.

**Consequences.** The report can be checked against a checkout of the data, but not against a publisher artifact, because none is published for this corpus. Anyone re-reading the report should treat `archive_sha256: null` as settled rather than as a missing step. Cost: if a published archive ever appears, this decision is superseded and the report gains a field it currently leaves empty.
