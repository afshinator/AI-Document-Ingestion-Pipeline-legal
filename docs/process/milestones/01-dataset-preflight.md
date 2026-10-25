# Milestone 01 — Dataset preflight

**Date:** 2026-10-25
**Status:** closed. `PASS_WITH_WARNINGS`, 0 failed checks, 1 recorded source discrepancy.
**Refs:** ADR-001, ADR-002, ADR-003, Session 002, Session 003

## What happened, in plain English

The repo started empty apart from a README and a copy of CUAD. The first real artifact was a preflight script whose job was to answer one question: is this dataset actually complete and internally consistent, before any extraction work depends on it.

Running it produced six errors. Investigating them was the actual work, and it went two ways. Most were the script's fault. It only looked for `.pdf` files, but 311 of the 510 PDFs are named `.PDF`, so it counted 199 and declared 312 contracts missing. It expected the JSON to have a `{"data": ...}` wrapper, but the file is a bare list, so it rejected a perfectly good file. And it matched CSV names to filenames too literally, so contracts present on disk were reported missing because the CSV keeps apostrophes and ampersands where the filenames use underscores.

Rewriting the matching rules collapsed six errors into two. Those two turned out to be real. One CSV row names a PDF that does not exist under that name, while the PDF on disk has an extra `_Option Agreement` suffix. And one of 13,822 answers in the JSON has an empty list where a string belongs.

For the first, the fix could have been to loosen matching or rename the file. Both were rejected: one hides future defects, the other edits the source data. Instead the two names were proven to be the same contract, and then a single hand-written override was added, with five rules that must pass or it is refused.

So the lesson from the second half is different from the first: some failures are your code being wrong about the data, and some are the data being wrong in ways no rule should paper over. Telling them apart is the skill. The dataset now passes: 510 files each way, 510 rows, 510 documents, one documented exception, one quarantined answer, and two questions left open on purpose.

## What works

- Every TXT decodes as UTF-8 and every PDF parses under `pypdf(strict=True)`: 510/510 each, 9,348 PDF pages, minimum 1 page, maximum 154.
- CSV is 510 rows x 83 columns with no empty identifiers, no case-insensitive duplicates, and no canonical collisions.
- All 510 rows resolve to a unique file in both formats. TXT: 499 exact + 11 punctuation-only differences. PDF: 501 exact + 8 punctuation-only + 1 verified override.
- `CUAD_v1.json` validates as a bare list: 510 documents, 510 paragraphs, 20,910 questions, 13,822 well-formed answers.
- 28 category spreadsheets present, matching the count in the dataset README.
- 20 unit tests cover the matching contract, override acceptance and rejection, and the malformed-answer policy. Two mutation checks were run against them: reverting `canonical_key()` to its first, wrong order fails one test; deleting the override ownership rule fails one test.
- The report is reproducible: two runs are byte-identical except `generated_utc`, and it records a SHA-256 per input plus the validator's own digest.

## What does not work, or is unresolved

- **3 evidence spans** in the Harpoon row (two `Know-How` definitions, the `Insurance` limits clause) were not found in either source file after alphanumeric normalization. Unresolved. Candidate explanations, none tested: text extraction loss, a different source version behind the CSV evidence, or a genuinely wrong span. Not to be restated as an annotation defect until one is ruled out.
- **1 malformed answer** at `data[316].paragraphs[0].qas[1]`: `{"text": [], "answer_start": 466}`. Recorded with coordinates; downstream span extraction must skip it rather than coerce it.
- **No publisher artifact exists** for the corpus (ADR-003), so `archive_sha256` is `null` and provenance rests on per-input digests.
- **18 of 19 non-exact matches are content-unverified.** Unique identifier resolution is not proof that the matched file is the document the CSV row intends. Only the Harpoon pair was verified to content level.
- **The 11 TXT pairs were never compared as content**, only resolved as unique filenames.
- **The 28 spreadsheets were counted, never opened.**
- The original v01 output report was deleted; its numbers are reproduced by re-running `scripts/inspect_dataset-01.py`.

## Open questions for the next phase

- Do the 3 unmatched spans indicate extraction loss, a source-version difference, or an annotation error? Resolving this requires rendering the PDF page, not another normalization.
- Are CSV evidence spans generally substrings of their source contract? Three of 62 in one row were not, which is either a local artifact or a corpus-wide property. Unknown which.
- Does the annotated `answer_start` point at the same text in the TXT that it points at in the PDF, given 0.9655 similarity between the two renditions? Nobody has checked an offset.

## Lessons worth carrying

- A validator's error count carries no information until its rules match how the data is actually written. Six errors meant nothing; two errors, after the rules were correct, meant something.
- Fixing a defect can install another. The first `canonical_key()` stripped the extension before the punctuation that followed it, so `.PDF'` keyed as `agreementpdf` — the exact case it existed to handle. The run did not catch it because a single row carried the stray quote; review did. A test now pins it.
- Reaching `PASS` did not require weakening a rule. Every failure was either a rule defect or an explicitly recorded exception.
- `PASS_WITH_WARNINGS` is a statement about files, structure and joins. It is not a statement about label correctness or fitness for evaluation.

## Next

A4: the relationship between source contracts and annotations — starting with an offset check and the three unmatched spans.
