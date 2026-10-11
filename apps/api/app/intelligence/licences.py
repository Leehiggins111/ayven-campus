"""Versions and licences for the packages Ayven actually imports.

AGPL components stay rejected. A name on this list is not a claim that the
adapter is the product.
"""

from __future__ import annotations

import importlib.metadata as metadata

LEDGER = (
    {"name": "fastapi", "licence": "MIT", "posture": "INTEGRATED"},
    {"name": "pydantic", "licence": "MIT", "posture": "INTEGRATED"},
    {"name": "llguidance", "licence": "MIT", "posture": "INTEGRATED"},
    {"name": "httpx", "licence": "BSD-3-Clause", "posture": "INTEGRATED"},
    {"name": "qwen-agent", "licence": "Apache-2.0", "posture": "ADAPTED"},
    {"name": "mcp", "licence": "MIT", "posture": "ADAPTED"},
    {"name": "browser-use", "licence": "MIT", "posture": "ADAPTED"},
    {"name": "crawl4ai", "licence": "Apache-2.0", "posture": "ADAPTED"},
    {"name": "fastembed", "licence": "Apache-2.0", "posture": "ADAPTED"},
    {"name": "pypdf", "licence": "BSD-3-Clause", "posture": "INTEGRATED"},
    {"name": "python-docx", "licence": "MIT", "posture": "INTEGRATED"},
    {"name": "openpyxl", "licence": "MIT", "posture": "INTEGRATED"},
    {"name": "trafilatura", "licence": "Apache-2.0", "posture": "INTEGRATED"},
    {"name": "pydantic-ai-slim", "licence": "MIT", "posture": "ADAPTED"},
    {"name": "pymupdf", "licence": "AGPL-3.0", "posture": "REJECTED"},
    {"name": "firecrawl", "licence": "AGPL-3.0", "posture": "REJECTED"},
)


def installed_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return ""


def ledger() -> list[dict]:
    rows = []
    for item in LEDGER:
        row = dict(item)
        row["installed"] = installed_version(item["name"])
        rows.append(row)
    return rows
