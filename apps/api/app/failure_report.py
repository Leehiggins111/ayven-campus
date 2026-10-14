"""report.md for a FAILED package, built from fixed wording.

No free text from the model, the report body, an error or a blocker is copied
into the report. Each failure is classified into a FailureType from structured
state, and the report shows that type's fixed sentence plus at most one short
detail taken from structured data (counts, exit codes, an exception class
name, a browser status word). The job title and brief pass a strict character
allowlist or are left out.
"""

from __future__ import annotations

import enum
import json
import re


class FailureType(str, enum.Enum):
    TESTS_FAILED = "tests_failed"
    BROWSER_CHECK_FAILED = "browser_check_failed"
    REVIEW_REJECTED = "review_rejected"
    MALFORMED_REVIEW = "malformed_review"
    STEP_ERROR = "step_error"
    MODEL_ERROR = "model_error"
    BUDGET_EXCEEDED = "budget_exceeded"
    TIMEOUT = "timeout"
    REWORK_REQUESTED = "rework_requested"
    OTHER = "other"


WORDING = {
    FailureType.TESTS_FAILED: "Build failed: the program's tests did not pass.",
    FailureType.BROWSER_CHECK_FAILED: "Build failed: the browser check did not pass.",
    FailureType.REVIEW_REJECTED: "The delivery review rejected the work.",
    FailureType.MALFORMED_REVIEW: "The delivery review did not return a valid result.",
    FailureType.STEP_ERROR: "A step failed after its retries.",
    FailureType.MODEL_ERROR: "The model could not be reached or did not reply.",
    FailureType.BUDGET_EXCEEDED: "The job stopped at its budget cap.",
    FailureType.TIMEOUT: "A step ran out of time.",
    FailureType.REWORK_REQUESTED: "Lee asked for the work to be redone.",
    FailureType.OTHER: "The job failed its checks.",
}

MAX_BLOCKERS = 5
_EXCEPTION = re.compile(r"\b([A-Z][A-Za-z]{2,38}(?:Error|Exception|Exceeded|Timeout))\b")
_BROWSER_STATUS = {"unavailable", "disabled", "error", "failed", "timeout", "not run"}
_ALLOWED = re.compile(r"^[A-Za-z0-9 .,:'£?!-]*$")


def exception_class(text: str) -> str:
    """The first exception class name in a text, and nothing else."""
    match = _EXCEPTION.search(text or "")
    return match.group(1) if match else ""


def classify(kind: str, text: str) -> FailureType:
    """Map a stored failure to a type. Only our own fixed prefixes and kinds are matched."""
    kind = (kind or "").lower()
    lowered = (text or "").lower()
    if kind == "cap" or "budget" in lowered or "spend cap" in lowered:
        return FailureType.BUDGET_EXCEEDED
    if kind == "rework":
        return FailureType.REWORK_REQUESTED
    if lowered.startswith("build failed: the program's tests"):
        return FailureType.TESTS_FAILED
    if lowered.startswith("build failed: the browser check"):
        return FailureType.BROWSER_CHECK_FAILED
    if "malformed_review" in lowered or "invalid review" in lowered:
        return FailureType.MALFORMED_REVIEW
    if "timeout" in lowered or "timed out" in lowered or "time limit" in lowered:
        return FailureType.TIMEOUT
    if lowered.startswith("review:") or " | review:" in lowered or "missing:" in lowered:
        return FailureType.REVIEW_REJECTED
    if lowered.startswith(("model-", "planning failed")) or "connecterror" in lowered or "connection refused" in lowered:
        return FailureType.MODEL_ERROR
    if " failed after " in lowered or lowered.startswith(("stopped during", "step error")):
        return FailureType.STEP_ERROR
    return FailureType.OTHER


def plain_detail(text: str, limit: int = 120) -> str:
    """Free text reduced to plain words, or nothing. Dropping beats cleverness."""
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value or not _ALLOWED.match(value):
        return ""
    tokens = value.split(" ")
    if any(len(token) >= 16 for token in tokens):
        return ""
    words = [t.strip(".,:'?!-") for t in tokens if len(t.strip(".,:'?!-")) >= 3]
    odd = [w for w in words if re.search(r"\d", w) and re.search(r"[A-Za-z]", w) or re.search(r".[A-Z]", w)]
    if words and len(odd) / len(words) > 0.2:
        return ""
    if len(value) > limit:
        value = value[:limit].rsplit(" ", 1)[0].rstrip(".,:'-") + "…"
    return value


def _detail(ftype: FailureType, text: str, evidence: dict, goal: dict | None) -> str:
    software = evidence.get("software_execution") or {}
    web = evidence.get("web_execution") or {}
    if ftype is FailureType.TESTS_FAILED:
        name = exception_class(str(software.get("stderr") or "")) or exception_class(text)
        code = software.get("exit_code")
        parts = [name] if name else []
        if isinstance(code, int) and not isinstance(code, bool):
            parts.append(f"exit code {code}")
        return ", ".join(parts)
    if ftype is FailureType.BROWSER_CHECK_FAILED:
        checks = [c for c in (web.get("checks") or []) if isinstance(c, dict)]
        status = str(web.get("status") or "not run").lower()
        status = status if status in _BROWSER_STATUS else "failed"
        if checks:
            failed = sum(1 for c in checks if not c.get("passed"))
            return f"{failed} of {len(checks)} checks failed"
        return f"browser {status}"
    if ftype is FailureType.BUDGET_EXCEEDED and goal:
        try:
            return f"spent £{float(goal.get('spent_gbp') or 0):.2f} of £{float(goal.get('job_cap_gbp') or 0):.2f}"
        except (TypeError, ValueError):
            return ""
    if ftype in (FailureType.STEP_ERROR, FailureType.MODEL_ERROR, FailureType.TIMEOUT, FailureType.OTHER):
        return exception_class(text)
    if ftype is FailureType.REVIEW_REJECTED:
        missing = re.search(r"missing:\s*([^|]*)", text or "", re.I)
        if missing:
            count = len([m for m in missing.group(1).split(";") if m.strip()])
            return f"{count} required item{'s' if count != 1 else ''} missing" if count else ""
    return ""


def _line(ftype: FailureType, detail: str) -> str:
    return WORDING[ftype] + (f" ({detail})" if detail else "")


def build(*, task_class: str, error: str, blockers: list[dict], evidence: dict, title: str, brief: str,
          goal: dict | None = None) -> str:
    software = evidence.get("software_execution") or {}
    web = evidence.get("web_execution") or {}
    is_program = task_class == "software_build" or bool(software)
    is_web = task_class in ("website_build", "application_build") or bool(web)

    typed = [(classify(b.get("kind") or "", b.get("reason") or ""), b.get("reason") or "") for b in blockers]
    if is_program and software.get("passed") is not True:
        reason_type = FailureType.TESTS_FAILED
    elif is_web and not (web.get("passed") is True and web.get("status") == "ok"):
        reason_type = FailureType.BROWSER_CHECK_FAILED
    elif error:
        reason_type = classify("", error)
        if reason_type is FailureType.OTHER and typed:
            reason_type = typed[0][0]
    elif typed:
        reason_type = typed[0][0]
    else:
        reason_type = FailureType.OTHER
    reason = _line(reason_type, _detail(reason_type, error, evidence, goal))

    seen, shown = set(), []
    for ftype, text in typed:
        if ftype in seen:
            continue
        seen.add(ftype)
        shown.append(_line(ftype, _detail(ftype, text, evidence, goal)))
    extra = max(0, len(shown) - MAX_BLOCKERS)  # distinct failure types beyond the cap
    shown = shown[:MAX_BLOCKERS]

    checks = []
    if is_program:
        passed = software.get("passed") is True
        checks.append(f"Tests: {'PASSED' if passed else 'FAILED'}")
        code = software.get("exit_code")
        if not passed and isinstance(code, int) and not isinstance(code, bool):
            checks.append(f"Test exit code: {code}")
    if is_web:
        passed = web.get("passed") is True and web.get("status") == "ok"
        status = str(web.get("status") or "not run").lower()
        status = status if status in _BROWSER_STATUS or status == "ok" else "failed"
        checks.append(f"Tests: {'PASSED' if passed else 'FAILED'}")
        checks.append(f"Browser check: {'passed' if passed else 'failed'} ({status})")

    if not checks and reason_type in (FailureType.TESTS_FAILED, FailureType.BROWSER_CHECK_FAILED):
        checks.append("Tests: FAILED")

    title = plain_detail(title, 120)
    brief = plain_detail(brief, 120)
    lines = ["# FAILED: this job did not pass its checks", "",
             "Nothing was delivered for approval. No source files or generated content are included.", ""]
    if title or brief:
        lines += ["## Job", ""]
        if title:
            lines.append(f"Title: {title}")
        if brief:
            lines.append(f"Brief: {brief}")
        lines.append("")
    lines += ["## Failure reason", "", reason, ""]
    if shown:
        lines += ["## Blockers", ""] + [f"- {b}" for b in shown]
        if extra > 0:
            lines.append(f"- +{extra} more")
        lines.append("")
    if checks:
        lines += ["## Checks", ""] + [f"- {c}" for c in checks] + [""]
    lines += ["Nothing was sent.", ""]
    return "\n".join(lines)


def evidence_from(observability_json: str) -> dict:
    try:
        data = json.loads(observability_json or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for key in ("software_execution", "web_execution"):
        if isinstance(data.get(key), dict) and data.get(key):
            out[key] = data[key]
    return out
