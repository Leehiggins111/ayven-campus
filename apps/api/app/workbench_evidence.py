"""Campus's own check that a Workbench PASS is backed by a trusted verification of the exact patch.

The worker's verdict, mode and evidence strings are claims. A PASS is only kept when the result carries the
Workbench server's signed verification record (qa-run.json) and every artifact it lists (changes.diff and the
five test logs), and Campus can confirm that:

* the record is signed with the Workbench attestation key (HMAC-SHA256 over the canonical record). The key is
  held by the Workbench server and Campus, never by the pull worker, so holding the worker token is not enough
  to forge a PASS;
* it was made for THIS order and revision (order_key), THIS allow-list, THIS order's owner acceptance tests
  (path + sha256 of each) and THIS order's exemptions (superseded tests, allowed removals);
* every artifact is byte-for-byte the one the record describes (sha256 + size), every run's log is the log that
  run produced, the patch digest matches, and the changed-file set equals the files named in the patch and stays
  inside the allow-list;
* the verification itself passed: the host-owned runner reported for every run; in the patched tree every test
  passed and every test that existed at the base revision still ran; the ORIGINAL tests (as at base) pass against
  the patched code; every owner acceptance test passes; no public definition was removed without the order's
  permission; no tamper findings.

Campus never executes code, so it does not re-run the tests: it verifies who ran them, on what, and what they
showed. record["coverage"] says how much that is; Campus passes it on to the owner unchanged.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path

from .workbench_scope import patch_paths

SCHEMA = "ayven.verification/v2"
PATCH, RECORD, LOG, BASELINE_LOG = "changes.diff", "qa-run.json", "qa-test.log", "baseline-test.log"
REGRESSION_LOG, ACCEPTANCE_LOG, ACCEPTANCE_BASE_LOG = ("regression-test.log", "acceptance-test.log",
                                                       "acceptance-baseline-test.log")
ARTIFACTS = (PATCH, LOG, BASELINE_LOG, REGRESSION_LOG, ACCEPTANCE_LOG, ACCEPTANCE_BASE_LOG)
RUN_LOGS = {"baseline": BASELINE_LOG, "verified": LOG, "regression": REGRESSION_LOG, "acceptance": ACCEPTANCE_LOG,
            "acceptance_baseline": ACCEPTANCE_BASE_LOG}


def acceptance_digest(tests) -> list:
    """What the record must bind for the order's owner acceptance tests (matches Workbench verify.py)."""
    return sorted(({"path": t["path"], "sha256": hashlib.sha256(t["content"].encode()).hexdigest()}
                   for t in tests or []), key=lambda t: t["path"])


def attestation_key():
    """AYVEN_ATTESTATION_KEY or the file named by AYVEN_ATTESTATION_KEY_FILE; None if absent or < 32 chars."""
    value = os.environ.get("AYVEN_ATTESTATION_KEY", "")
    path = os.environ.get("AYVEN_ATTESTATION_KEY_FILE", "")
    if not value and path:
        try:
            value = Path(path).read_text().strip()
        except OSError:
            value = ""
    return value.encode() if len(value) >= 32 else None


def canonical(record) -> bytes:
    """Matches the Workbench's verify.canonical."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def key_id(key: bytes) -> str:
    return hashlib.sha256(b"ayven-attestation-key-id:" + key).hexdigest()[:16]


def request_key(order_id: str, revision: int = 0) -> str:
    """The Workbench request key for one revision of an order: one Workbench goal per revision."""
    raw = order_id if not revision else f"{order_id}:r{revision}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _passing(outcome, before):
    return outcome == "passed" or (outcome in ("skipped", "xfailed") and before == outcome)


def _real(run):
    return [t for t in (run or {}).get("collected") or [] if not str(t).startswith("collect::")]


def test_evidence_problems(artifacts: dict, *, order_id=None, revision=0, allowed=None, key=None,
                           acceptance=None, superseded=(), allowed_removals=()) -> list:
    """[] when the artifacts prove a trusted, passing verification of the exported patch, else the reasons."""
    key = key if key is not None else attestation_key()
    missing = [name for name in (RECORD, *ARTIFACTS) if name not in artifacts]
    if missing:
        return ["PASS result is missing verification evidence: " + ", ".join(missing)]
    try:
        envelope = json.loads(artifacts[RECORD].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return [f"{RECORD} is not valid JSON"]
    record = envelope.get("record") if isinstance(envelope, dict) else None
    if not isinstance(record, dict):
        return [f"{RECORD} is not a verification record"]
    signature = envelope.get("signature")
    if not key:
        return ["Campus has no attestation key configured, so it cannot verify who ran the tests"]
    if not isinstance(signature, dict) or not isinstance(signature.get("value"), str):
        return ["The verification record is not signed by the Workbench"]
    expected = hmac.new(key, canonical(record), hashlib.sha256).hexdigest()
    if signature.get("key_id") != key_id(key) or not hmac.compare_digest(signature["value"], expected):
        return ["The verification record's signature is invalid (forged, altered or signed with another key)"]
    problems = []
    if record.get("schema") != SCHEMA:
        problems.append(f"Unknown verification schema {str(record.get('schema'))[:40]!r}")
    if record.get("verdict") != "PASS" or record.get("reasons"):
        problems.append(f"The trusted verification did not pass ({record.get('verdict')!r}): "
                        + "; ".join(str(r) for r in (record.get("reasons") or []))[:300])
    if record.get("tamper_findings"):
        problems.append("The change interferes with test execution: " + "; ".join(record["tamper_findings"])[:300])
    if order_id is not None and record.get("order_key") != request_key(order_id, revision):
        problems.append("The verification record was made for a different order or revision")
    if allowed is not None and sorted(record.get("allowed_files") or []) != sorted(allowed):
        problems.append("The verification record was made under a different allow-list")
    manifest = record.get("artifacts") or {}
    for name in ARTIFACTS:
        meta, raw = manifest.get(name) or {}, artifacts[name]
        if meta.get("sha256") != hashlib.sha256(raw).hexdigest() or meta.get("bytes") != len(raw):
            problems.append(f"{name} is not byte-for-byte the artifact the record describes (cut or changed)")
    if record.get("patch_sha256") != hashlib.sha256(artifacts[PATCH]).hexdigest():
        problems.append("The tests were not run on this exact patch")
    verified, baseline = record.get("verified") or {}, record.get("baseline") or {}
    for run_name, log_name in RUN_LOGS.items():
        run = record.get(run_name)
        digest = hashlib.sha256(artifacts[log_name]).hexdigest()
        if run is None:
            if artifacts[log_name]:
                problems.append(f"{log_name} has content but the record has no {run_name} run")
        elif not isinstance(run, dict) or run.get("log_sha256") != digest:
            problems.append(f"{log_name} is not the log the {run_name} run produced")
        elif not run.get("reported"):
            problems.append(f"The host-owned test runner did not report the {run_name} run")
    if not verified.get("reported") or not baseline.get("reported"):
        problems.append("The host-owned test runner did not report a result")
    if verified.get("exit_code") != 0:
        problems.append(f"The verified test run exited {verified.get('exit_code')!r}")
    outcomes, collected = verified.get("outcomes") or {}, verified.get("collected") or []
    tests = _real(verified)
    if not tests:
        problems.append("No tests ran")
    before = baseline.get("outcomes") or {}
    failing = sorted(t for t, o in outcomes.items() if not _passing(o, before.get(t)))
    if failing or any(t not in outcomes for t in tests):
        problems.append("Not every test passed: " +
                        ", ".join(failing[:5] or [t for t in tests if t not in outcomes][:5]))
    superseded = sorted(superseded or [])
    if sorted(record.get("superseded_tests") or []) != superseded:
        problems.append("The verification record was made with different superseded tests than the order")
    lost = sorted(set(baseline.get("collected") or []) - set(collected) - set(superseded))
    if lost:
        problems.append("Tests present at the base revision no longer run: " + ", ".join(lost[:5]))
    # The original tests, exactly as at the base revision, run against the patched code.
    regression = record.get("regression")
    if not isinstance(regression, dict):
        problems.append("The record has no run of the original tests against the patched code")
    else:
        r_out = regression.get("outcomes") or {}
        r_bad = sorted(t for t, o in r_out.items() if t not in superseded and not _passing(o, before.get(t)))
        r_lost = sorted(set(baseline.get("collected") or []) - set(regression.get("collected") or [])
                        - set(superseded))
        if r_bad or r_lost:
            problems.append("Original tests fail or no longer run against the patched code: " +
                            ", ".join((r_bad + r_lost)[:5]))
    # The owner's acceptance tests: exactly the ones this order carries, all passing.
    expected = acceptance_digest(acceptance)
    if (record.get("acceptance_tests") or []) != expected:
        problems.append("The verification record was not made with this order's acceptance tests")
    accepted = record.get("acceptance")
    if expected:
        a_tests = _real(accepted)
        a_out = (accepted or {}).get("outcomes") or {}
        if not isinstance(accepted, dict) or not a_tests:
            problems.append("The owner acceptance tests did not run")
        elif any(a_out.get(t) != "passed" for t in a_tests) or any(o != "passed" for o in a_out.values()):
            problems.append("Owner acceptance tests fail: " +
                            ", ".join(t for t in a_tests if a_out.get(t) != "passed")[:300])
    elif accepted is not None:
        problems.append("The record has an acceptance run the order did not ask for")
    allowed_removals = sorted(allowed_removals or [])
    if sorted(record.get("allowed_removals") or []) != allowed_removals:
        problems.append("The verification record was made with different allowed removals than the order")
    removed = [r for r in record.get("removed_definitions") or [] if r not in allowed_removals]
    if removed:
        problems.append("The patch removes existing definitions: " + ", ".join(removed[:5]))
    if not isinstance(record.get("coverage"), dict) or not record["coverage"].get("level"):
        problems.append("The record does not say what its tests covered")
    try:
        named, special = patch_paths(artifacts[PATCH].decode("utf-8"))
    except UnicodeDecodeError:
        named, special = [], ["patch is not UTF-8"]
    changed = sorted(c.get("path") for c in record.get("changed_files") or [] if isinstance(c, dict))
    if special or changed != sorted(set(named)):
        problems.append("The record's changed files do not match the patch")
    if allowed is not None and [p for p in changed if p not in allowed]:
        problems.append("The record shows changes outside the allow-list")
    return problems


def record_of(artifacts: dict):
    """The signed record inside qa-run.json (call only after test_evidence_problems returned [])."""
    return json.loads(artifacts[RECORD].decode("utf-8"))["record"]
