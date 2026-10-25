#!/usr/bin/env python3
"""CUAD v1 inventory / structural preflight (Milestone 1, Step A3).

VERSION 02 — supersedes inspect_dataset-01.py. The v01 logic is preserved
verbatim except where marked `CHANGED (v2)` below. Three defects were found by
running v01 against the actual dataset (510 txt, 510 pdf, 510 CSV rows):

  1. PDF discovery was case-sensitive. v01 globbed `*.pdf` and found 199 of 510
     because 311 files are named `.PDF`. pathlib glob is case-sensitive on POSIX
     regardless of the filesystem, so this produced a false UNEXPECTED_FILE_COUNT
     plus 312 false ID_MISSING errors. v2 discovers by `suffix.lower()`.
  2. CUAD_v1.json has no `{"data": [...]}` wrapper. The file on disk is a bare
     list of 510 {title, paragraphs} objects, so v01 reported a false
     INVALID_JSON. v2 accepts either shape and records which one it saw.
  3. CSV identifiers and on-disk filenames were sanitized differently, so
     stem `casefold()` alone did not match: CSV keeps `'`, `&`, `(`, `)` where
     the filename uses `_`, and some CSV cells carry a trailing space or a stray
     quote *after* the extension (`... AGREEMENT.PDF'`). v2 matches on an
     alphanumeric-only canonical key and classifies how each pair differed,
     rather than collapsing all non-exact matches into one bucket.

What this does NOT do: see the v01 notes below. The three fixes change how
names are compared; they do not make the dataset's own labels more correct, and
they do not normalize away a genuine content mismatch (e.g. a CSV row naming
"Development Agreement" where disk has "Development Agreement_Option
Agreement"). Such rows still surface as errors — that is intended.

WHAT this script does:
  * Confirms the expected CUAD files and contract directories exist.
  * Checks that every TXT can be decoded and every PDF can be parsed by pypdf.
  * Checks CSV structure and maps its contract identifiers to TXT / PDF files.
  * Validates the high-level SQuAD structure of CUAD_v1.json.
  * Records archive SHA-256 if you provide the original ZIP.
  * Writes a machine-readable report and exits nonzero on integrity errors.

WHY: Catch bad assumptions about the dataset *before* building labels/evals.

WHAT it does NOT do:
  * It does not check the legal correctness or completeness of CUAD's labels.
  * It does not verify that every PDF page renders or extract PDF/OCR text.
  * It does not verify JSON answer offsets or map them to original TXT offsets.
  * It does not prove a ZIP's authenticity unless you independently compare its
    computed hash with a trusted, published hash for the same archive.

INSTALL: uv sync
RUN:
  uv run python scripts/inspect_dataset-02.py --data-dir data/raw/CUAD_v1 \
      --output reports/dataset-inventory.json
  # CHANGED (v2): the example no longer passes --archive. No published archive
  # of the 510-contract corpus could be located (the Atticus GitHub data.zip
  # holds only three SQuAD JSONs; Hugging Face stores the corpus exploded), so
  # the flag has nothing legitimate to hash. Omit it: the report records
  # archive_sha256 = null and a NO_ARCHIVE warning.

RERUN: after changing/downloaded files, after fixing a validation error, and
before freezing a dataset snapshot or reproducing published experiments.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from pypdf import PdfReader
except ImportError:
    sys.exit("Missing dependency: install pypdf with `python -m pip install pypdf`.")

# WHAT: Dataset expectations according to the CUAD v1 README.
# WHY: They catch missing/incomplete releases. These are about the *release*,
# not assumptions about clause frequency or model performance.
EXPECTED_CONTRACTS = 510
EXPECTED_COLUMNS = 83  # 1 contract identifier + 2 per each of 41 categories
EXPECTED_XLSX = 28
DATASET_URL = "https://www.atticusprojectai.org/cuad/"
RESEARCH_REPO = "https://github.com/The-Atticus-Project/cuad"
# License attribution source: Atticus dataset listing on Hugging Face.
# Report as publisher-listed metadata, not as a machine-verified license.
LICENSE = "CC BY 4.0 (publisher's Hugging Face dataset listing; verify for your archive)"

# WHAT: Narrow, auditable exceptions where a CSV identifier does not equal the
# source filename, after verifying that both name the same contract.
#
# WHY: CUAD's CSV and the extracted files were sanitized independently, and for a
# small number of contracts that produced genuinely different names rather than
# cosmetic punctuation differences. The alternative was renaming source files
# (mutates the dataset), editing the CSV (mutates the labels), or loosening the
# matching rules (would silently join unrelated contracts). See ADR-002.
#
# HOW: Each entry is CSV identifier -> exact source filename plus the evidence
# that was checked. An entry is applied only if it validates (unique target,
# target not already claimed by another row, identifier present in the CSV);
# otherwise it is rejected and the original mismatch stays reported as an error.
# The mismatch remains in report["source_discrepancies"] as a warning either way.
#
# SCOPE: PDF only, one entry. Not a pattern-matching mechanism: no substring,
# prefix, or similarity fallback exists anywhere in this script.
CONTRACT_ID_OVERRIDES: dict[str, dict[str, str]] = {
    "HarpoonTherapeuticsInc_20200312_10-K_EX-10.18_12051356_EX-10.18_Development Agreement.PDF": {
        "file": "HarpoonTherapeuticsInc_20200312_10-K_EX-10.18_12051356_EX-10.18_Development Agreement_Option Agreement.pdf",
        "reason": "CSV identifier omits the '_Option Agreement' suffix present on the source PDF",
        # Verified 2026-10-25, not inferred from the filename:
        #  - CSV row's own Document Name-Answer is "DEVELOPMENT AND OPTION AGREEMENT".
        #  - The TXT named exactly as the CSV identifier and this PDF are the same
        #    document: identical first 300 chars, 248,383 vs 248,527 chars,
        #    difflib ratio 0.9655 over the first 60k chars, both containing
        #    EXHIBIT 10.18, 2019-11-20, AbbVie Biotechnology Ltd, Harpoon
        #    Therapeutics; PDF text contains the title DEVELOPMENT AND OPTION
        #    AGREEMENT.
        #  - 59 of the row's 62 recorded evidence spans appear in both files.
        #  - One CSV row references Harpoon; no other disk file canonicalizes to
        #    the suffixed PDF.
        "evidence": "CSV Document Name-Answer = 'DEVELOPMENT AND OPTION AGREEMENT'; CSV-named TXT and suffixed PDF identical at 0.9655 similarity over 60k chars; 59/62 evidence spans shared",
    },
}


def display_path(path: Path) -> str:
    """HOW: Keep machine-specific absolute paths out of a report meant to be committed.

    WHY: The report is evidence checked into the repository, so it has to be
    readable on another machine. Paths that escape the repo (or the cwd) fall
    back to their absolute form rather than pretending to be relative.
    """
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def add_issue(report: dict[str, Any], level: str, code: str, message: str) -> None:
    """HOW: Collect machine-readable problems and human-readable explanations."""
    report["issues"].append({"level": level, "code": code, "message": message})


def sha256(path: Path) -> str:
    """HOW: Stream an archive in chunks to avoid loading a large ZIP into RAM."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_identifier(value: str) -> str:
    """HOW: Compare filenames without extension or case; never claim this is exact.

    The CSV filename may end in .pdf, .txt, or neither. Do NOT use Path.stem on
    extensionless identifiers because it can truncate identifiers with dots.

    CHANGED (v2): kept for *classification and reporting only*. It is no longer
    the matching key, because it cannot bridge the CSV-vs-filename punctuation
    differences (see canonical_key). Order of operations still matters: this
    strips surrounding whitespace *before* removing the extension, so a name like
    "... AGREEMENT .PDF" keeps its inner trailing space — which is precisely why
    it failed to match in v01.
    """
    value = value.strip().replace("\\", "/").split("/")[-1]
    if value.lower().endswith((".pdf", ".txt")):
        value = value[:-4]
    return value


# CHANGED (v2): new. The single matching key used for both the disk index and
# the CSV identifiers.
#
# WHY: the CSV keeps characters that the filename sanitizer replaced with "_"
# (apostrophes, "&", "/", parentheses), and a few CSV cells carry a stray quote
# or space *after* the extension. Comparing raw text therefore reports "missing"
# for a contract that is present. Reducing to [a-z0-9] removes exactly those
# differences and nothing that distinguishes two contracts.
#
# WHY NOT full "fuzzy" matching: it would hide real defects. A genuine content
# mismatch — CSV says "Development Agreement", disk says "Development
# Agreement_Option Agreement" — still fails to match, and still reported.
#
# RISK: alphanumeric reduction could merge two different names. That is why
# index_files keeps *all* paths per key and any key with more than one path is
# reported as AMBIGUOUS_DISK_NAME instead of silently picking one. The same risk
# applies to CSV identifiers and is covered by find_canonical_collisions().
#
# FIXED (v2, second pass): the first version stripped the extension before
# removing noise that follows it, so "...AGREEMENT.PDF'" kept ".PDF" and keyed
# as "agreementpdf" — the exact case the fix was meant to handle. Order is now
# noise, extension, noise, reduce. Caught in review, not by the run, because only
# one CSV cell had the stray quote.
def canonical_key(value: str) -> str:
    """HOW: Extension-stripped, punctuation-stripped, case-insensitive match key."""
    value = value.strip().replace("\\", "/").split("/")[-1]
    # Remove stray punctuation that follows the extension, e.g. "AGREEMENT.PDF'".
    value = value.rstrip(" '\"._")
    # Remove only recognized extensions; nothing else is treated as one.
    if value.lower().endswith((".pdf", ".txt")):
        value = value[:-4]
    # Remove trailing whitespace left before the extension, e.g. "AGREEMENT .txt".
    value = value.rstrip(" '\"._")
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def find_canonical_collisions(names: list[str]) -> dict[str, list[str]]:
    """HOW: Group identifiers by canonical key and return only keys shared by >1.

    WHAT: Distinct CSV identifiers can reduce to one key ("ABC-DEF.pdf" and
    "ABC_DEF.pdf" both key to "abcdef").

    WHY: For disk files that means two candidates for one contract; for CSV rows
    it means two rows competing to describe one contract file. Both are
    ambiguities a human must resolve, not something to silently collapse.

    WARNING: An empty result does not prove the identifiers are distinct — only
    that they are distinct after canonicalization.
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for name in names:
        if name:
            groups[canonical_key(name)].append(name)
    return {key: group for key, group in groups.items() if len(group) > 1}


def index_files(files: list[Path]) -> dict[str, list[Path]]:
    """HOW: Preserve all files for each name; dictionaries with one value hide collisions.

    CHANGED (v2): keys are canonical_key() instead of Path.stem.casefold().
    """
    index: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        index[canonical_key(path.stem)].append(path)
    return index


def check_contract_files(root: Path, folder: str, extension: str, report: dict[str, Any]) -> tuple[list[Path], dict[str, list[Path]]]:
    """WHAT: Inventory actual contract files; verify entire text or PDF structure.

    WHY: Merely finding a file or reading ten bytes does not establish that a
    contract is accessible. PDF parsing is a basic structural check, not a
    promise of renderability or text-extraction quality.
    """
    location = root / folder
    if not location.is_dir():
        add_issue(report, "error", "MISSING_DIRECTORY", f"Missing directory: {location}")
        return [], {}

    # CHANGED (v2): was `location.rglob(f"*{extension}")`, which is case-sensitive
    # on POSIX. 311 of the 510 PDFs are named `.PDF`, so v01 counted 199 and
    # reported 312 contracts as missing. Match on the lowercased suffix instead,
    # and keep a tally of the case variants so the next reader can see why.
    files = sorted(p for p in location.rglob("*") if p.is_file() and p.suffix.lower() == extension)
    extension_cases = dict(Counter(p.suffix for p in files))

    info: dict[str, Any] = {"count": len(files), "bytes": 0, "checked": 0,
                            "extension_cases": extension_cases, "failures": []}
    report["inventory"][folder] = info
    if len(files) != EXPECTED_CONTRACTS:
        add_issue(report, "error", "UNEXPECTED_FILE_COUNT", f"{folder}: expected {EXPECTED_CONTRACTS} {extension} files, found {len(files)}")

    for path in files:
        try:
            info["bytes"] += path.stat().st_size
            if path.stat().st_size == 0:
                raise ValueError("zero-byte file")
            if extension == ".txt":
                # HOW: Read the complete text, not just a prefix.
                path.read_text(encoding="utf-8-sig")
            else:
                # HOW: pypdf parses PDF cross-reference info and page tree.
                # Checking len(reader.pages) forces discovery of the page count.
                reader = PdfReader(str(path), strict=True)
                if reader.is_encrypted:
                    raise ValueError("encrypted PDF; cannot inspect without password")
                if len(reader.pages) < 1:
                    raise ValueError("PDF has no pages")
            info["checked"] += 1
        except Exception as exc:
            info["failures"].append({"file": str(path.relative_to(root)), "reason": str(exc)})
            add_issue(report, "error", "UNREADABLE_FILE", f"{path.relative_to(root)}: {exc}")

    # Detect collisions before attempting to match anything to CSV identifiers.
    indexed = index_files(files)
    for key, matches in indexed.items():
        if len(matches) > 1:
            add_issue(report, "error", "AMBIGUOUS_DISK_NAME", f"{folder}: canonical key {key!r} matches {len(matches)} files: {[p.name for p in matches]}")
    return files, indexed


def match_id(name: str, indexed: dict[str, list[Path]]) -> dict[str, Any]:
    """HOW: Identify unique exact-stem matches separately from case-only matches.

    CHANGED (v2): three-way classification instead of exact/other, so the report
    distinguishes a cosmetic difference (safe to proceed) from a real one.
      exact        raw identifier equals the file stem
      case_mismatch only letter case differs
      punct_mismatch punctuation/sanitization differs ('&' vs '_', stray quote,
                     trailing space before the extension)
    Only `missing` and `ambiguous` are errors; the other two are warnings.
    """
    stem = normalize_identifier(name)
    paths = indexed.get(canonical_key(name), [])
    if not paths:
        return {"status": "missing", "name": name}
    if len(paths) != 1:
        return {"status": "ambiguous", "name": name, "candidates": [str(p) for p in paths]}
    if paths[0].stem == stem:
        status = "exact"
    elif paths[0].stem.casefold() == stem.casefold():
        status = "case_mismatch"
    else:
        status = "punct_mismatch"
    return {"status": status, "name": name, "file": paths[0].name}


def resolve_overrides(names: list[str], index: dict[str, list[Path]]) -> tuple[dict[str, Path], list[dict[str, str]]]:
    """WHAT: Validate CONTRACT_ID_OVERRIDES against the CSV and the disk index.

    WHY: An override is a claim that two different names mean one contract. It is
    only safe if the claim survives every check below; a rejected override must
    fall back to the normal rules so the mismatch keeps failing loudly instead of
    disappearing.

    HOW: Returns (applied, rejected). `applied` maps CSV identifier to source
    path. `rejected` carries one record per refused override with the rule that
    refused it.

    ACCEPTANCE RULES:
      1. The identifier exists in the CSV.
      2. The target resolves to exactly one file on disk.
      3. No other CSV identifier already claims that target.
      4. No two overrides point at the same target.
      5. The entry carries a documented reason and evidence.
    """
    applied: dict[str, Path] = {}
    rejected: list[dict[str, str]] = []
    claimed: set[str] = set()
    csv_keys = {canonical_key(n): n for n in names if n}

    for name, spec in CONTRACT_ID_OVERRIDES.items():
        target = spec.get("file", "")
        reason = spec.get("reason", "").strip()
        evidence = spec.get("evidence", "").strip()
        record = {"identifier": name, "target": target}

        if name not in names:
            rejected.append({**record, "rule": "identifier not present in CSV"})
            continue
        if not reason or not evidence:
            rejected.append({**record, "rule": "missing documented reason or evidence"})
            continue
        matches = [p for paths in index.values() for p in paths if p.name == target]
        if len(matches) != 1:
            rejected.append({**record, "rule": f"target resolves to {len(matches)} files, expected 1"})
            continue
        target_key = canonical_key(target)
        if target_key in claimed:
            rejected.append({**record, "rule": "target already assigned by another override"})
            continue
        other_owner = csv_keys.get(target_key)
        if other_owner and other_owner != name:
            rejected.append({**record, "rule": f"target already claimed by CSV identifier {other_owner!r}"})
            continue

        claimed.add(target_key)
        applied[name] = matches[0]
    return applied, rejected


def check_csv(root: Path, txt_index: dict[str, list[Path]], pdf_index: dict[str, list[Path]], report: dict[str, Any]) -> None:
    """WHAT: Validate CSV shape, identifier uniqueness, and cross-file mapping.

    WHY: A CSV row is only usable downstream if it identifies one unambiguous
    source TXT and PDF. Duplicate IDs or silently changed capitalization matter.
    """
    path = root / "master_clauses.csv"
    if not path.is_file():
        add_issue(report, "error", "MISSING_CSV", f"Missing {path}")
        return
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = csv.reader(stream)
            header = next(rows)
            names: list[str] = []
            bad_rows: list[int] = []
            for line_no, row in enumerate(rows, start=2):
                if len(row) != len(header):
                    bad_rows.append(line_no)
                names.append(row[0].strip() if row else "")
    except (OSError, UnicodeError, csv.Error, StopIteration) as exc:
        add_issue(report, "error", "INVALID_CSV", f"Cannot parse {path}: {exc}")
        return

    counts = Counter(n.casefold() for n in names)
    duplicates = [name for name, count in counts.items() if count > 1]
    # ADDED (v2, second pass): case-insensitive duplicate detection misses
    # identifiers that differ only in punctuation and still describe one file.
    canonical_collisions = find_canonical_collisions(names)
    # ADDED (v2, third pass): apply verified overrides before matching, and keep
    # what the plain rules would have said so the discrepancy stays visible.
    pdf_overrides, rejected_overrides = resolve_overrides(names, pdf_index)
    for rejected in rejected_overrides:
        add_issue(report, "error", "OVERRIDE_REJECTED",
                  f"Override for {rejected['identifier']!r} refused: {rejected['rule']}; mismatch left as an error")
    txt_matches = [match_id(n, txt_index) for n in names if n]
    pdf_matches: list[dict[str, Any]] = []
    original_status: dict[str, str] = {}
    for n in names:
        if not n:
            continue
        if n in pdf_overrides:
            original_status[n] = match_id(n, pdf_index)["status"]
            pdf_matches.append({"status": "explicit_override", "name": n,
                                "file": pdf_overrides[n].name,
                                "original_status": original_status[n]})
        else:
            pdf_matches.append(match_id(n, pdf_index))

    # One record per underlying data discrepancy, so the summary does not imply
    # that two failed checks mean two defects.
    report["source_discrepancies"] = [
        {
            "contract": n,
            "issue": "CSV identifier differs from source PDF filename",
            "csv_identifier": n,
            "source_file": path.name,
            "original_status": original_status.get(n, "unknown"),
            "resolution": "explicit_override",
            "verified": True,
            "reason": CONTRACT_ID_OVERRIDES[n]["reason"],
            "evidence": CONTRACT_ID_OVERRIDES[n]["evidence"],
        }
        for n, path in pdf_overrides.items()
    ]
    for discrepancy in report["source_discrepancies"]:
        add_issue(report, "warning", "SOURCE_DISCREPANCY",
                  f"{discrepancy['contract']} -> {discrepancy['source_file']}: "
                  f"{discrepancy['reason']} (resolved by verified explicit override)")

    report["csv"] = {
        "rows": len(names), "columns": len(header), "identifier_column": header[0],
        "empty_ids": sum(not n for n in names), "duplicate_ids_case_insensitive": duplicates,
        "canonical_id_collisions": canonical_collisions,
        "bad_row_numbers": bad_rows,
        "txt_matching": dict(Counter(m["status"] for m in txt_matches)),
        "pdf_matching": dict(Counter(m["status"] for m in pdf_matches)),
        "mismatches": {"txt": [m for m in txt_matches if m["status"] != "exact"],
                       "pdf": [m for m in pdf_matches if m["status"] != "exact"]},
    }
    if len(header) != EXPECTED_COLUMNS or len(names) != EXPECTED_CONTRACTS:
        add_issue(report, "error", "CSV_SHAPE", f"CSV shape: {len(names)} rows x {len(header)} cols; expected {EXPECTED_CONTRACTS} x {EXPECTED_COLUMNS}")
    if bad_rows:
        add_issue(report, "error", "CSV_ROW_WIDTH", f"{len(bad_rows)} CSV rows have incorrect column counts (first: {bad_rows[:10]})")
    if duplicates or any(not n for n in names):
        add_issue(report, "error", "INVALID_IDS", f"CSV duplicate IDs: {duplicates[:5]}; empty IDs: {sum(not n for n in names)}")
    # ADDED (v2, second pass): treated as an error, not a warning, because two CSV
    # rows mapping to one contract file cannot both be right.
    if canonical_collisions:
        add_issue(report, "error", "CANONICAL_ID_COLLISION",
                  f"CSV identifiers collide after canonicalization: {list(canonical_collisions.items())[:5]}")
    for kind, matches in (("TXT", txt_matches), ("PDF", pdf_matches)):
        for status, count in Counter(m["status"] for m in matches).items():
            # explicit_override is counted in the match tallies and reported once
            # as SOURCE_DISCREPANCY; it is not a failure of the join.
            if status not in ("exact", "explicit_override"):
                add_issue(report, "error" if status in ("missing", "ambiguous") else "warning",
                          "ID_" + status.upper(), f"{kind}: {count} {status} filename matches")
    # Reverse completeness: ensure all on-disk contract files appear in the CSV.
    # CHANGED (v2): canonical_key() on both sides, matching index_files().
    # CHANGED (v2, third pass): an override target counts as referenced by its CSV
    # identifier, which is the whole point of the override.
    ids = {canonical_key(n) for n in names if n}
    ids |= {canonical_key(path.name) for path in pdf_overrides.values()}
    for kind, index in (("TXT", txt_index), ("PDF", pdf_index)):
        extras = sorted(set(index) - ids)
        if extras:
            add_issue(report, "error", "UNREFERENCED_FILE", f"{kind}: {len(extras)} disk stems not referenced by CSV, examples {extras[:5]}")


def check_json(root: Path, report: dict[str, Any]) -> None:
    """WHAT: Validate expected outer SQuAD containers, not the truth of answers.

    CHANGED (v2): accepts a bare top-level list as well as SQuAD v1's
    {"data": [...], "version": ...} wrapper. CUAD_v1.json as distributed is a
    list of 510 {title, paragraphs} objects with no wrapper, so v01's shape check
    produced a false INVALID_JSON on a complete, valid file. The observed shape
    is recorded in the report so a future format change stays visible.

    CHANGED (v2, second pass): an ill-typed *individual answer* no longer aborts
    validation. One of ~13k answer objects in this release has
    {"text": [], "answer_start": 466} — an empty list where SQuAD requires a
    string. Raising there discarded all counts and reported the whole file as
    invalid, which is the wrong blast radius: structure is sound, one label is
    not. Structural problems still raise; content anomalies are collected into
    report["json"]["malformed_answers"] and reported as warnings, with their
    document/paragraph/question coordinates.
    """
    path = root / "CUAD_v1.json"
    if not path.is_file():
        add_issue(report, "error", "MISSING_JSON", f"Missing {path}")
        return
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            obj = json.load(stream)
        # CHANGED (v2): which wrapper the file uses.
        if isinstance(obj, list):
            documents = obj
            shape = "list"
            top_level_keys: list[str] = []
            version = None
        elif isinstance(obj, dict) and isinstance(obj.get("data"), list):
            documents = obj["data"]
            shape = "data_wrapper"
            top_level_keys = sorted(obj)
            version = obj.get("version")
        else:
            raise ValueError("expected a 'data' array or a top-level list")
        paragraphs = questions = answers = 0
        malformed_answers: list[dict[str, Any]] = []
        for i, doc in enumerate(documents):
            if not isinstance(doc, dict) or not isinstance(doc.get("paragraphs"), list):
                raise ValueError(f"data[{i}] lacks a paragraphs array")
            if not isinstance(doc.get("title"), str):
                raise ValueError(f"data[{i}] lacks a string title")
            for j, paragraph in enumerate(doc["paragraphs"]):
                if not isinstance(paragraph, dict) or not isinstance(paragraph.get("context"), str) or not isinstance(paragraph.get("qas"), list):
                    raise ValueError(f"data[{i}].paragraphs[{j}] malformed")
                paragraphs += 1
                for k, qa in enumerate(paragraph["qas"]):
                    if not isinstance(qa, dict) or not isinstance(qa.get("question"), str) or not isinstance(qa.get("answers"), list):
                        raise ValueError(f"data[{i}].paragraphs[{j}].qas[{k}] malformed")
                    questions += 1
                    for answer in qa["answers"]:
                        # CHANGED (v2, second pass): collect instead of abort.
                        if not isinstance(answer, dict) or not isinstance(answer.get("text"), str) or not isinstance(answer.get("answer_start"), int):
                            malformed_answers.append({
                                "document": i, "paragraph": j, "question": k,
                                "text_type": type(answer.get("text")).__name__ if isinstance(answer, dict) else type(answer).__name__,
                                "answer_start_type": type(answer.get("answer_start")).__name__ if isinstance(answer, dict) else None,
                            })
                            continue
                        answers += 1
        report["json"] = {"shape": shape, "top_level_keys": top_level_keys, "version": version,
                          "documents": len(documents), "paragraphs": paragraphs,
                          "questions": questions, "answer_objects": answers,
                          "malformed_answers": {"count": len(malformed_answers),
                                                "locations": malformed_answers[:10]}}
        if malformed_answers:
            first = malformed_answers[0]
            add_issue(report, "warning", "MALFORMED_ANSWER",
                      f"{len(malformed_answers)} answer object(s) are not {{text: str, answer_start: int}}; "
                      f"first at data[{first['document']}].paragraphs[{first['paragraph']}].qas[{first['question']}] "
                      f"(text is {first['text_type']}). Downstream span extraction must skip these.")
        if len(documents) != EXPECTED_CONTRACTS:
            add_issue(report, "warning", "JSON_DOCUMENT_COUNT", f"JSON data contains {len(documents)} entries, expected {EXPECTED_CONTRACTS}; inspect representation")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        add_issue(report, "error", "INVALID_JSON", f"Cannot validate {path}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw/CUAD_v1"), help="CUAD dataset root containing master_clauses.csv")
    parser.add_argument("--archive", type=Path, help="Original CUAD ZIP, for SHA-256 checksum; no default guessing")
    parser.add_argument("--output", type=Path, default=Path("reports/dataset-inventory.json"), help="Machine-readable report location")
    args = parser.parse_args()
    root = args.data_dir.expanduser().resolve()
    report: dict[str, Any] = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        # CHANGED (v2, third pass): repo-relative, so the committed report is
        # readable on another machine.
        "dataset_root": display_path(args.data_dir.expanduser()),
        "script": "inspect_dataset-02.py",
        "provenance": {"dataset": "CUAD v1", "source_url": DATASET_URL,
                       "research_repository": RESEARCH_REPO, "license": LICENSE,
                       "archive_sha256": None, "archive_path": None},
        # CHANGED (v2, third pass): one entry per underlying data discrepancy,
        # distinct from the count of failed validation checks.
        "source_discrepancies": [],
        # ADDED (v2, fourth pass): state what a pass means, next to the pass.
        # A unique identifier resolution is not evidence that the matched file is
        # the same underlying contract; only source_discrepancies carries that
        # kind of evidence, and only for the entries recorded there.
        "validation_scope": {
            "establishes": "file presence, TXT decodability, PDF parseability, unique identifier resolution, JSON structure, CSV shape",
            "does_not_establish": "that labels are correct; that a resolved match is the same underlying document as the CSV row intends; that the corpus is fit for unbiased model evaluation",
        },
        "inventory": {}, "csv": None, "json": None, "issues": [],
    }
    if args.archive:
        try:
            if not args.archive.is_file():
                raise FileNotFoundError(args.archive)
            report["provenance"]["archive_path"] = display_path(args.archive)
            report["provenance"]["archive_sha256"] = sha256(args.archive)
        except OSError as exc:
            add_issue(report, "error", "ARCHIVE_CHECKSUM", f"Cannot hash archive: {exc}")
    else:
        add_issue(report, "warning", "NO_ARCHIVE", "Archive not provided; ZIP checksum not checked")

    if not root.is_dir():
        add_issue(report, "error", "MISSING_ROOT", f"Dataset directory does not exist: {root}")
    else:
        # WHAT: Explicit folders stop arbitrary .txt/.pdf elsewhere in the ZIP
        # from inflating expected contract counts.
        _, txt = check_contract_files(root, "full_contract_txt", ".txt", report)
        _, pdf = check_contract_files(root, "full_contract_pdf", ".pdf", report)
        check_csv(root, txt, pdf, report)
        check_json(root, report)
        xlsx_dir = root / "label_group_xlsx"
        sheets = list(xlsx_dir.glob("*.xlsx")) if xlsx_dir.is_dir() else []
        report["inventory"]["label_group_xlsx"] = {"count": len(sheets), "content_validated": False}
        if len(sheets) != EXPECTED_XLSX:
            add_issue(report, "warning", "XLSX_COUNT", f"Expected {EXPECTED_XLSX} category spreadsheets; found {len(sheets)}")

    errors = sum(i["level"] == "error" for i in report["issues"])
    warnings = sum(i["level"] == "warning" for i in report["issues"])
    report["summary"] = {"status": "FAIL" if errors else "PASS_WITH_WARNINGS" if warnings else "PASS",
                         "errors": errors, "warnings": warnings,
                         # ADDED (v2, third pass): checks and discrepancies are not
                         # the same quantity. PASS_WITH_WARNINGS means the structural
                         # preflight passed; it says nothing about label correctness.
                         "failed_checks": errors,
                         "source_discrepancies": len(report["source_discrepancies"])}

    # HOW: Output directories are created; source files are never modified.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"CUAD inventory: {report['summary']['status']} ({errors} errors, {warnings} warnings)")
    print(f"Source discrepancies: {report['summary']['source_discrepancies']}")
    if report["csv"]:
        print(f"CSV: {report['csv']['rows']} rows, {report['csv']['columns']} columns")
        print(f"TXT matches: {report['csv']['txt_matching']}")
        print(f"PDF matches: {report['csv']['pdf_matching']}")
    if report["json"]:
        print(f"JSON: {report['json']['documents']} documents, {report['json']['questions']} questions, {report['json']['answer_objects']} answer objects")
    for issue in report["issues"][:20]:
        print(f"{issue['level'].upper()}: {issue['code']}: {issue['message']}")
    if len(report["issues"]) > 20:
        print(f"... {len(report['issues']) - 20} further issues in JSON report")
    print(f"Report: {args.output}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
