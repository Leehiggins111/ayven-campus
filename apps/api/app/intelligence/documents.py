"""Document extraction. Stdlib handles text, CSV, and HTML. Heavier parsers stay optional.

PyMuPDF is AGPL and is not imported. Docling and unstructured are not required.
"""

from __future__ import annotations

import csv
import io
from html.parser import HTMLParser
from pathlib import Path


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data and data.strip():
            self.parts.append(data.strip())


def extract(path: str | None = None, data: bytes | None = None, name: str = "") -> dict:
    raw_name = name or (path or "")
    suffix = Path(raw_name).suffix.lower()
    blob = data if data is not None else (Path(path).read_bytes() if path else b"")
    if suffix in {".txt", ".md", ""} and not suffix.endswith("pdf"):
        text = blob.decode("utf-8", errors="replace")
        return _ok(text, raw_name, "text")
    if suffix == ".csv":
        text = blob.decode("utf-8", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
        rendered = "\n".join(" | ".join(row) for row in rows[:40])
        return _ok(rendered, raw_name, "csv", tables=rows[:40])
    if suffix in {".html", ".htm"}:
        parser = _Text()
        parser.feed(blob.decode("utf-8", errors="replace"))
        return _ok("\n".join(parser.parts), raw_name, "html")
    if suffix == ".pdf":
        return {
            "ok": False,
            "kind": "pdf",
            "text": "",
            "error": "pdf_extractor_not_installed",
            "licence_note": "PyMuPDF is AGPL and is not bundled. A MIT extractor such as markitdown can be added as an optional extra.",
            "source": raw_name,
        }
    return {"ok": False, "kind": suffix or "unknown", "text": "", "error": "unsupported_document", "source": raw_name}


def _ok(text: str, source: str, kind: str, tables: list | None = None) -> dict:
    return {
        "ok": True,
        "kind": kind,
        "text": text[:8000],
        "tables": tables or [],
        "source": source,
        "page": 1 if kind != "text" else None,
    }


def status() -> dict:
    return {
        "backend": "stdlib",
        "formats": ["txt", "md", "csv", "html"],
        "pdf": "OPTIONAL_NOT_INSTALLED",
        "pymupdf": "REJECTED_AGPL",
        "markitdown": "OPTIONAL",
        "docling": "DEFERRED",
        "unstructured": "DEFERRED",
    }
