# /// script
# requires-python = ">=3.10"
# dependencies = ["pypdf==7.0.1"]
# ///
"""Import the full Form 10-K from Alphabet's official annual report PDF."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
import os
from urllib.request import Request, urlopen

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
PDF_URL = "https://s206.q4cdn.com/479360582/files/doc_financials/2025/Alphabet-GOOG-_AR_2025_WO2_TRD_WR.pdf"
SEC_URL = "https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/goog-20251231.htm"
PDF_PATH = ROOT / "data" / "raw" / "alphabet-2025-10k.pdf"
CORPUS_PATH = ROOT / "data" / "alphabet-2025-10k.json"
PDF_SHA256 = "3f497fbc4a6922ebe13e3a532b073ccf55a40f7ff69b11a2cf37aac13222e2cf"


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def bookmarks(reader, entries):
    result = []
    for item in entries:
        if isinstance(item, list):
            result.extend(bookmarks(reader, item))
        else:
            result.append((reader.get_destination_page_number(item), str(item["/Title"]).strip()))
    return result


def build_corpus(pdf_path):
    checksum = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    if checksum != PDF_SHA256:
        raise ValueError("PDF checksum differs from the verified FY2025 report. Existing corpus preserved.")
    reader = PdfReader(pdf_path)
    outline = bookmarks(reader, reader.outline)
    start = next(page for page, title in outline if title == "FORM 10-K")
    end = next(page for page, title in outline if title.startswith("Directors and executive"))
    pages = []
    line_number = 1
    note = ""
    previous_section = ""
    for index in range(start, end):
        text = reader.pages[index].extract_text(extraction_mode="layout")
        # Keep one PDF row per source line, including comparative year headers.
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
        if lines and re.fullmatch(
            r"(?:(?:[ivxlcdm]+|\d+)(?: Alphabet 2025 Annual Report)?|Alphabet 2025 Annual Report(?: \d+)?)",
            lines[-1],
        ):
            lines.pop()
        if not lines:
            raise ValueError(f"No searchable text on PDF page {index + 1}.")
        section = next(
            (title for page, title in reversed(outline) if page <= index and title.startswith(("Item ", "Signatures", "FORM 10-K", "Table of Contents", "Note About"))),
            "Form 10-K",
        )
        if section != previous_section:
            note = ""
        previous_section = section
        notes = [line for line in lines if re.match(r"^Note \d+\.", line)]
        headings = list(dict.fromkeys([section] + ([note] if note else []) + notes))
        if notes:
            note = notes[-1]
        pages.append({
            "id": f"pdf-{index + 1:03d}", "pdf_page": index + 1,
            "section": " > ".join(headings), "start_line": line_number,
            "lines": lines,
        })
        line_number += len(lines)
    if not any("future principal payments" in " ".join(page["lines"]) for page in pages):
        raise ValueError("The imported filing is missing its debt maturity disclosure.")
    return {
        "format_version": 1,
        "source": {
            "title": "Alphabet Inc. FY2025 Form 10-K",
            "reporting_date": "2025-12-31", "sec_url": SEC_URL, "pdf_url": PDF_URL,
            "pdf_sha256": checksum,
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "parser": "pypdf 7.0.1, layout extraction; whitespace normalized within rows",
            "pdf_page_count": len(reader.pages),
            "included_pdf_pages": [start + 1, end],
        },
        "passages": pages,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=PDF_PATH, help="Use a local copy of the official PDF")
    args = parser.parse_args()
    if not args.pdf.exists():
        args.pdf.parent.mkdir(parents=True, exist_ok=True)
        request = Request(PDF_URL, headers={"User-Agent": "CreditDiligenceLab/0.2"})
        with urlopen(request, timeout=60) as response:
            data = response.read()
        if not data.startswith(b"%PDF-") or hashlib.sha256(data).hexdigest() != PDF_SHA256:
            parser.exit(1, "Downloaded PDF did not match the verified source. Existing corpus preserved.\n")
        atomic_write(args.pdf, data)
    try:
        corpus = build_corpus(args.pdf)
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    atomic_write(CORPUS_PATH, (json.dumps(corpus, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"Imported {len(corpus['passages'])} full-filing page passages to {CORPUS_PATH}.")


if __name__ == "__main__":
    main()
