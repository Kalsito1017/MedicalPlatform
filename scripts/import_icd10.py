#!/usr/bin/env python3
"""Import the official NCPHA ICD-10 XLSX into a local JSON catalogue.

This script is intentionally run by a deployment owner, not by the app or CI.
The NCPHA website terms restrict reproduction without prior written consent;
do not commit the generated full catalogue or the source XLSX to the repository.
The app always ships with a small attributed demo subset
(``backend/app/data/icd10_bg_seed.json``); this script creates the optional
``backend/app/data/icd10_bg.json`` full file locally (gitignored).

Usage:
    python scripts/import_icd10.py /path/to/ICD10_08062026.xlsx \
        --version "NCPHA-2026-08-06" \
        --retrieved 2026-11-16

Optional update DOCX files (2025/2026 new/modified codes):
    python scripts/import_icd10.py ... \
        --update /path/to/New_code_MKB10_2025.docx \
        --update /path/to/New_codes_2026.docx

The importer uses only the Python standard library (zipfile + XML), so it does
not add an Excel dependency to the application.
"""
from __future__ import annotations

import argparse
import json
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CODE_RE = re.compile(r"^[A-Z][0-9]{2}(?:\.[0-9A-Z]{1,4})?$")
DOCX_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for si in root.findall(f"{{{NS_MAIN}}}si"):
        values.append("".join(node.text or "" for node in si.iter(f"{{{NS_MAIN}}}t")))
    return values


def _workbook_sheets(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    relmap = {rel.get("Id"): rel.get("Target") for rel in rels}
    sheets: list[tuple[str, str]] = []
    for sheet in workbook.find(f"{{{NS_MAIN}}}sheets"):
        name = sheet.get("name") or ""
        rid = sheet.get(f"{{{NS_REL}}}id")
        target = relmap.get(rid or "")
        if not target:
            continue
        if not target.startswith("xl/"):
            target = f"xl/{target.lstrip('/')}"
        sheets.append((name, target))
    return sheets


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    value = cell.find(f"{{{NS_MAIN}}}v")
    raw = value.text if value is not None else ""
    if cell.get("t") == "s" and raw:
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    return raw or ""


def _parse_xlsx(path: Path) -> dict[str, tuple[str, str]]:
    """Return {code: (name_bg, source_sheet)} from the official XLSX."""
    entries: dict[str, tuple[str, str]] = {}
    with zipfile.ZipFile(path) as zf:
        shared = _shared_strings(zf)
        sheets = _workbook_sheets(zf)
        # Prefer the combined sheet when present; it contains both 3- and
        # 4-character rubrics. Fall back to all sheets.
        ordered = sorted(sheets, key=lambda item: (0 if "всички" in item[0].lower() else 1))
        for name, target in ordered:
            root = ET.fromstring(zf.read(target))
            for row in root.findall(f".//{{{NS_MAIN}}}sheetData/{{{NS_MAIN}}}row"):
                cells = row.findall(f"{{{NS_MAIN}}}c")
                if len(cells) < 2:
                    continue
                code = _cell_value(cells[0], shared).strip().upper()
                label = " ".join(_cell_value(cells[1], shared).split())
                if not CODE_RE.match(code) or not label:
                    continue
                # Do not overwrite a better/combined-sheet description.
                entries.setdefault(code, (label, name))
    return entries


def _parse_update_docx(path: Path) -> dict[str, tuple[str, str]]:
    """Best-effort extraction of code + description lines from an update DOCX.

    The NCPHA update documents are prose, not tables. Only paragraphs that
    begin with a valid ICD-10 code are imported; headings/notes are skipped.
    The output must be spot-checked by a clinical owner.
    """
    entries: dict[str, tuple[str, str]] = {}
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    for paragraph in root.iter(f"{{{DOCX_NS}}}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{{{DOCX_NS}}}t"))
        text = " ".join(text.split())
        if not text:
            continue
        match = re.match(r"^([A-Z][0-9]{2}(?:\.[0-9A-Z]{1,4})?)\s*(.+)$", text)
        if not match:
            continue
        code = match.group(1).upper()
        label = match.group(2).strip(" .;:-")
        if len(label) < 3:
            continue
        entries.setdefault(code, (label, path.name))
    return entries


def _level(code: str) -> str:
    if "." not in code:
        return "three"
    return "four" if len(code.split(".", 1)[1]) == 1 else "five"


def _write_catalogue(
    output: Path,
    entries: dict[str, tuple[str, str]],
    *,
    version: str,
    source_url: str,
    retrieved: str,
    source_documents: list[str],
) -> None:
    payload = {
        "metadata": {
            "classification": "ICD-10",
            "classification_bg": "МКБ-10",
            "source_organization": "Национален център по обществено здраве и анализи (НЦОЗА)",
            "source_url": source_url,
            "source_documents": source_documents,
            "version": version,
            "publication_year": 2026,
            "retrieved_at": retrieved,
            "scope": "Full local import from the official NCPHA XLSX and optional update documents. Not committed to the repository.",
            "license_note": "NCPHA website terms restrict reproduction without prior written consent. This file is a local deployment import; do not redistribute it.",
            "regulatory_reference": "Наредба № 42 от 2004 г.; verify the latest amendments and effective dates in Държавен вестник before production use.",
        },
        "entries": [
            {
                "code": code,
                "name_bg": name,
                "name_en": None,
                "status": "active",
                "level": _level(code),
                "source": source,
            }
            for code, (name, source) in sorted(entries.items())
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx", type=Path, help="Official NCPHA ICD-10 .xlsx file")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("backend/app/data/icd10_bg.json"),
        help="Output JSON path (gitignored)",
    )
    parser.add_argument("--version", default="NCPHA-2026-08-06", help="Classification version label")
    parser.add_argument(
        "--source-url",
        default="https://ncpha.government.bg/index/301-mkb-10-mejdunarodna-statisticheska-klasifikacia-na-bolestite-i-problemite-svurzani-sus-zdraveto.html",
    )
    parser.add_argument(
        "--retrieved",
        default=date.today().isoformat(),
        help="Retrieval date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--update",
        type=Path,
        action="append",
        default=[],
        help="Optional NCPHA 2025/2026 update .docx (repeatable)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.xlsx.is_file():
        parser.error(f"XLSX not found: {args.xlsx}")
    entries = _parse_xlsx(args.xlsx)
    documents = [args.xlsx.name]
    for update in args.update:
        if not update.is_file():
            parser.error(f"update DOCX not found: {update}")
        entries.update(_parse_update_docx(update))
        documents.append(update.name)

    if not entries:
        parser.error("no ICD-10 entries were parsed")
    _write_catalogue(
        args.output,
        entries,
        version=args.version,
        source_url=args.source_url,
        retrieved=args.retrieved,
        source_documents=documents,
    )
    print(f"wrote {len(entries)} ICD-10 entries to {args.output}")
    print("The full catalogue is gitignored; do not redistribute it without permission.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
