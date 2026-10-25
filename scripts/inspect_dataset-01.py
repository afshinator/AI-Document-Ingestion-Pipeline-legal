#!/usr/bin/env python3
"""CUAD v1 inventory / structural preflight (Milestone 1, Step A3).

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
  uv run python scripts/inspect_dataset.py --data-dir data/raw/CUAD_v1 \
      --archive CUAD_v1.zip --output reports/dataset-inventory.json
  # If the archive was discarded, omit --archive; checksum is then "not checked".

RERUN: after changing/downloaded files, after fixing a validation error, and
before freezing a dataset snapshot or reproducing published experiments.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
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
    """
    value = value.strip().replace("\\", "/").split("/")[-1]
    if value.lower().endswith((".pdf", ".txt")):
        value = value[:-4]
    return value


def index_files(files: list[Path]) -> dict[str, list[Path]]:
    """HOW: Preserve all files for each name; dictionaries with one value hide collisions."""
    index: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        index[path.stem.casefold()].append(path)
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

    files = sorted(location.rglob(f"*{extension}"))
    info: dict[str, Any] = {"count": len(files), "bytes": 0, "checked": 0, "failures": []}
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
            add_issue(report, "error", "AMBIGUOUS_DISK_NAME", f"{folder}: case-insensitive stem {key!r} matches {len(matches)} files: {[p.name for p in matches]}")
    return files, indexed


def match_id(name: str, indexed: dict[str, list[Path]]) -> dict[str, Any]:
    """HOW: Identify unique exact-stem matches separately from case-only matches."""
    stem = normalize_identifier(name)
    paths = indexed.get(stem.casefold(), [])
    if not paths:
        return {"status": "missing", "name": name}
    if len(paths) != 1:
        return {"status": "ambiguous", "name": name, "candidates": [str(p) for p in paths]}
    return {"status": "exact" if paths[0].stem == stem else "case_mismatch", "name": name, "path": str(paths[0])}


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
    txt_matches = [match_id(n, txt_index) for n in names if n]
    pdf_matches = [match_id(n, pdf_index) for n in names if n]
    report["csv"] = {
        "rows": len(names), "columns": len(header), "identifier_column": header[0],
        "empty_ids": sum(not n for n in names), "duplicate_ids_case_insensitive": duplicates,
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
    for kind, matches in (("TXT", txt_matches), ("PDF", pdf_matches)):
        for status, count in Counter(m["status"] for m in matches).items():
            if status != "exact":
                add_issue(report, "error" if status in ("missing", "ambiguous") else "warning",
                          "ID_" + status.upper(), f"{kind}: {count} {status} filename matches")
    # Reverse completeness: ensure all on-disk contract files appear in the CSV.
    ids = {normalize_identifier(n).casefold() for n in names if n}
    for kind, index in (("TXT", txt_index), ("PDF", pdf_index)):
        extras = sorted(set(index) - ids)
        if extras:
            add_issue(report, "error", "UNREFERENCED_FILE", f"{kind}: {len(extras)} disk stems not referenced by CSV, examples {extras[:5]}")


def check_json(root: Path, report: dict[str, Any]) -> None:
    """WHAT: Validate expected outer SQuAD containers, not the truth of answers."""
    path = root / "CUAD_v1.json"
    if not path.is_file():
        add_issue(report, "error", "MISSING_JSON", f"Missing {path}")
        return
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            obj = json.load(stream)
        if not isinstance(obj, dict) or not isinstance(obj.get("data"), list):
            raise ValueError("expected an object with a 'data' array")
        documents = obj["data"]
        paragraphs = questions = answers = 0
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
                        if not isinstance(answer, dict) or not isinstance(answer.get("text"), str) or not isinstance(answer.get("answer_start"), int):
                            raise ValueError(f"malformed answer in data[{i}].paragraphs[{j}].qas[{k}]")
                        answers += 1
        report["json"] = {"top_level_keys": sorted(obj), "version": obj.get("version"),
                          "documents": len(documents), "paragraphs": paragraphs,
                          "questions": questions, "answer_objects": answers}
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
        "dataset_root": str(root),
        "provenance": {"dataset": "CUAD v1", "source_url": DATASET_URL,
                       "research_repository": RESEARCH_REPO, "license": LICENSE,
                       "archive_sha256": None, "archive_path": None},
        "inventory": {}, "csv": None, "json": None, "issues": [],
    }
    if args.archive:
        try:
            if not args.archive.is_file():
                raise FileNotFoundError(args.archive)
            report["provenance"]["archive_path"] = str(args.archive.resolve())
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
                         "errors": errors, "warnings": warnings}

    # HOW: Output directories are created; source files are never modified.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"CUAD inventory: {report['summary']['status']} ({errors} errors, {warnings} warnings)")
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
