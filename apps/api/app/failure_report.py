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

NEXT = {
    FailureType.TESTS_FAILED: "Retry the job; if it fails again, make the brief's expected behaviour more specific.",
    FailureType.BROWSER_CHECK_FAILED: "Retry once the browser check can run; if the app itself failed, adjust the brief.",
    FailureType.REVIEW_REJECTED: "Adjust the brief or the missing criteria, then retry the job.",
    FailureType.MALFORMED_REVIEW: "Retry the job. If the review fails again, check the review model returns JSON, or switch the supervisor model.",
    FailureType.STEP_ERROR: "Retry the job; if it fails at the same step again, report it.",
    FailureType.MODEL_ERROR: "Check the model server is running, then retry the job.",
    FailureType.BUDGET_EXCEEDED: "Raise this job's budget cap, then retry the job.",
    FailureType.TIMEOUT: "Retry the job; if it times out again, simplify the brief or raise the time limit.",
    FailureType.REWORK_REQUESTED: "Ayven will redo the work using your reason.",
    FailureType.OTHER: "Retry the job; if it fails again, report it.",
}

MAX_BLOCKERS = 5
_EXCEPTION = re.compile(r"\b([A-Z][A-Za-z]{2,38}(?:Error|Exception|Exceeded|Timeout))\b")
_BROWSER_STATUS = {"unavailable", "disabled", "error", "failed", "timeout", "not run"}
_ALLOWED = re.compile(r"^[A-Za-z0-9 .,:'£?!()\[\]-]*$")


import builtins as _builtins

KNOWN_EXCEPTIONS = frozenset(
    name for name, value in vars(_builtins).items()
    if isinstance(value, type) and issubclass(value, BaseException)
) | frozenset({
    # Third-party and library errors Ayven's own stack raises.
    "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout", "ReadError", "WriteError",
    "RemoteProtocolError", "HTTPStatusError", "HTTPError", "RequestError", "TransportError", "TimeoutException",
    "ProxyError", "TooManyRedirects", "JSONDecodeError", "ValidationError", "OperationalError", "IntegrityError",
    "DatabaseError", "ProgrammingError", "CalledProcessError", "TimeoutExpired", "SubprocessError", "CancelledError",
    "InvalidStateError", "IncompleteReadError", "LimitOverrunError", "QueueEmpty", "QueueFull", "BadZipFile",
    "UnpicklingError", "PicklingError", "Empty", "Full", "Error", "StaleWrite", "BudgetExceeded",
    "PlaywrightError", "TargetClosedError",
})


def exception_class(text: str) -> str:
    """The first exception class name in a text, if it is a known class; otherwise 'a custom error'."""
    match = _EXCEPTION.search(text or "")
    if not match:
        return ""
    return match.group(1) if match.group(1) in KNOWN_EXCEPTIONS else "a custom error"


def exit_code_text(code) -> str:
    """0-255 as is; a negative code as the signal that killed the process; anything else unknown."""
    import signal

    if isinstance(code, bool) or not isinstance(code, int):
        return "exit code unknown"
    if 0 <= code <= 255:
        return f"exit code {code}"
    if code < 0:
        try:
            return f"killed by {signal.Signals(-code).name}"
        except ValueError:
            return "exit code unknown"
    return "exit code unknown"


def count_text(failed, total, noun: str = "checks") -> str:
    """'N of M <noun> failed' only for sane counts; otherwise nothing."""
    if isinstance(failed, bool) or isinstance(total, bool) or not isinstance(failed, int) or not isinstance(total, int):
        return ""
    if not (0 < total <= 1000 and 0 <= failed <= total):
        return ""
    return f"{failed} of {total} {noun} failed"


def classify(kind: str, text: str) -> FailureType:
    """Map a stored failure to a type. Only our own fixed prefixes and kinds are matched."""
    kind = (kind or "").lower()
    lowered = (text or "").lower()
    if kind == "cap" or "budget" in lowered or "spend cap" in lowered:
        return FailureType.BUDGET_EXCEEDED
    if kind == "timeout":
        return FailureType.TIMEOUT
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
    if re.search(r"\w[(\[]", value):  # a call or an index such as f(x) or a[0]; '(77a)' after a space is fine
        return ""
    words = [t.strip(".,:'?!-()[]") for t in tokens if len(t.strip(".,:'?!-()[]")) >= 3]
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
        parts = [name] if name else []
        if "exit_code" in software:
            parts.append(exit_code_text(software.get("exit_code")))
        return ", ".join(parts)
    if ftype is FailureType.BROWSER_CHECK_FAILED:
        checks = [c for c in (web.get("checks") or []) if isinstance(c, dict)]
        status = str(web.get("status") or "not run").lower()
        status = status if status in _BROWSER_STATUS else "failed"
        if checks:
            counted = count_text(sum(1 for c in checks if not c.get("passed")), len(checks))
            if counted:
                return counted
        return f"browser {status}"
    if ftype is FailureType.BUDGET_EXCEEDED and goal:
        try:
            return f"spent £{float(goal.get('spent_gbp') or 0):.2f} of £{float(goal.get('job_cap_gbp') or 0):.2f}"
        except (TypeError, ValueError):
            return ""
    if ftype in (FailureType.STEP_ERROR, FailureType.MODEL_ERROR, FailureType.TIMEOUT, FailureType.OTHER):
        return exception_class(text)
    if ftype in (FailureType.REVIEW_REJECTED, FailureType.MALFORMED_REVIEW):
        return missing_criteria(text, (goal or {}).get("acceptance_criteria") or [])
    return ""


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9£]+", (text or "").lower()) if len(w) >= 3}


def missing_criteria(text: str, criteria: list) -> str:
    """Which of the customer's own criteria the review reported missing.

    The review's wording is only matched against the criteria; what is printed is the
    criterion number and the customer's criterion text through the strict allowlist.
    """
    match = re.search(r"missing:\s*([^|]*)", text or "", re.I)
    if not match:
        return ""
    items = [m.strip() for m in re.split(r"[;\n]", match.group(1)) if m.strip()]
    if not items:
        return ""
    labels, unmatched = [], 0
    for item in items:
        words = _words(item)
        best, score = None, 0.0
        for number, criterion in enumerate(criteria, start=1):
            target = _words(str(criterion))
            if not target or not words:
                continue
            overlap = len(words & target) / len(target)
            if overlap > score:
                best, score = number, overlap
        if best is not None and score >= 0.5:
            label = plain_detail(str(criteria[best - 1]), 60)
            entry = f"criterion {best}" + (f": {label}" if label else "")
            if entry not in labels:
                labels.append(entry)
        else:
            unmatched += 1
    if not labels:
        return f"{len(items)} required item{'s' if len(items) != 1 else ''} missing"
    shown = "; ".join(labels[:3])
    more = max(0, len(labels) - 3) + unmatched
    return f"missing {shown}" + (f"; +{more} more" if more > 0 else "")


MODEL_TIMEOUT_WORDING = "Stopped: a model call timed out."

_IMPERATIVE = ("raise", "retry", "check", "read", "adjust", "fix", "answer", "approve", "add", "remove", "give",
               "confirm", "stop", "set", "switch", "simplify", "reply", "choose", "update", "rerun", "re-run")


def _line(ftype: FailureType, detail: str, text: str = "") -> str:
    wording = WORDING[ftype]
    if ftype is FailureType.TIMEOUT and "model call timed out" in (text or "").lower():
        wording = MODEL_TIMEOUT_WORDING
        detail = ""
    return wording + (f" ({detail})" if detail else "")


def build(*, task_class: str, error: str, blockers: list[dict], evidence: dict, title: str, brief: str,
          goal: dict | None = None) -> str:
    software = evidence.get("software_execution") or {}
    web = evidence.get("web_execution") or {}
    is_program = task_class == "software_build" or bool(software)
    is_web = task_class in ("website_build", "application_build") or bool(web)

    typed = [(classify(b.get("kind") or "", b.get("reason") or ""), b.get("reason") or "", b.get("needed_from_lee") or "")
             for b in blockers]
    error_type = classify("", error) if error else None
    stop_types = (FailureType.BUDGET_EXCEEDED, FailureType.TIMEOUT)
    if error_type in stop_types:
        reason_type = error_type
    elif any(t in stop_types for t, _, _ in typed) and not (software or web):
        reason_type = next(t for t, _, _ in typed if t in stop_types)
    elif software and software.get("passed") is not True:
        reason_type = FailureType.TESTS_FAILED
    elif web and not (web.get("passed") is True and web.get("status") == "ok"):
        reason_type = FailureType.BROWSER_CHECK_FAILED
    elif is_program and not software and not error and not typed:
        reason_type = FailureType.TESTS_FAILED
    elif is_web and not web and not error and not typed:
        reason_type = FailureType.BROWSER_CHECK_FAILED
    elif error:
        reason_type = classify("", error)
        if reason_type is FailureType.OTHER and typed:
            reason_type = typed[0][0]
    elif typed:
        reason_type = typed[0][0]
    else:
        reason_type = FailureType.OTHER
    reason_text = error if error_type is reason_type else next((t for f, t, _ in typed if f is reason_type), error)
    reason = _line(reason_type, _detail(reason_type, reason_text, evidence, goal), reason_text)
    next_step = NEXT[reason_type]
    for ftype, _text, needed in typed:
        if ftype is reason_type and needed:
            # Our own needed_from_lee wording, when it passes the allowlist.
            needed = plain_detail(needed, 120)
            # Only an instruction to Lee is used; a noun phrase such as 'A valid delivery review' is not actionable.
            if needed and needed.split(" ", 1)[0].lower().rstrip(",.:") in _IMPERATIVE:
                next_step = needed
            break

    seen, shown = set(), []
    for ftype, text, _needed in typed:
        if ftype in seen:
            continue
        seen.add(ftype)
        shown.append(_line(ftype, _detail(ftype, text, evidence, goal), text))
    shown = shown[:MAX_BLOCKERS]
    extra = len(typed) - len(shown)  # every blocker not shown, same type or not

    checks = []
    build_ran = reason_type in (FailureType.TESTS_FAILED, FailureType.BROWSER_CHECK_FAILED)
    if is_program and not software and not build_ran:
        checks.append("Tests: not run")
    elif is_program and software:
        passed = software.get("passed") is True
        checks.append(f"Tests: {'PASSED' if passed else 'FAILED'}")
        if not passed and "exit_code" in software:
            checks.append("Test " + exit_code_text(software.get("exit_code")).replace("exit code ", "exit code: "))
    if is_web and not web and not build_ran:
        if not is_program:
            checks.append("Tests: not run")
        checks.append("Browser check: not run")
    elif is_web and (web or build_ran):
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
    lines += ["## Failure reason", "", reason, "", "## What next", "", next_step, ""]
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
