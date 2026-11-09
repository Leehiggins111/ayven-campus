"""Campus's own check that a Workbench PASS is backed by a trusted verification of the exact patch.

The worker's verdict, mode and evidence strings are claims. A PASS is only kept when the result carries the
Workbench server's signed verification record (qa-run.json) and every artifact it lists (changes.diff,
qa-test.log, baseline-test.log), and Campus can confirm that:

* the record is signed with the Workbench attestation key (HMAC-SHA256 over the canonical record). The key is
  held by the Workbench server and Campus, never by the pull worker, so holding the worker token is not enough
  to forge a PASS;
* it was made for THIS order and revision (order_key) and THIS allow-list;
* every artifact is byte-for-byte the one the record describes (sha256 + size), the patch digest matches, and
  the changed-file set in the record equals the files named in the patch and stays inside the allow-list;
* the verification itself passed: the host-owned runner reported, exit code 0, every test passed, at least one
  test ran, and every test that existed at the base revision still ran; no tamper findings.

Campus never executes code, so it does not re-run the tests: it verifies who ran them and on what.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path

from .workbench_scope import patch_paths

SCHEMA = "ayven.verification/v1"
PATCH, RECORD, LOG, BASELINE_LOG = "changes.diff", "qa-run.json", "qa-test.log", "baseline-test.log"
ARTIFACTS = (PATCH, LOG, BASELINE_LOG)


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
    return outcome in ("passed", "xfailed") or (outcome == "skipped" and before == "skipped")


TEST_OVERRIDE = "AYVEN_TEST_ACCEPT_UNVERIFIED_MODEL"


def signed_record(artifacts: dict, key=None):
    """The verification record in qa-run.json if its Workbench signature verifies with Campus's key, else None."""
    key = key if key is not None else attestation_key()
    raw = artifacts.get(RECORD)
    if not key or raw is None:
        return None
    try:
        envelope = json.loads(raw.decode("utf-8"))
        record, signature = envelope["record"], envelope.get("signature") or {}
    except (UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if not isinstance(record, dict) or not isinstance(signature, dict) or not isinstance(signature.get("value"), str):
        return None
    expected = hmac.new(key, canonical(record), hashlib.sha256).hexdigest()
    if signature.get("key_id") != key_id(key) or not hmac.compare_digest(signature["value"], expected):
        return None
    return record


def provenance_mode(record) -> str:
    """D12: REAL only when the signed record shows a verified Ollama model produced the change. A missing or
    unverified provenance is SIMULATED: the worker's own REAL/SIMULATED label is never trusted."""
    prov = (record or {}).get("model_provenance") or {}
    return "REAL" if prov.get("kind") == "ollama" and not prov.get("problems") else "SIMULATED"


def unverified_model_test_override() -> bool:
    """Scripted-model drills only: AYVEN_TEST_ACCEPT_UNVERIFIED_MODEL=1 with AYVEN_ENV=test lets a result from an
    unverified model be treated as REAL, and every such result is marked. Refused at start-up in any other mode."""
    return os.environ.get(TEST_OVERRIDE) == "1" and os.environ.get("AYVEN_ENV", "").strip().lower() == "test"


def test_evidence_problems(artifacts: dict, *, order_id=None, revision=0, allowed=None, key=None) -> list:
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
    # Regression guard (release 20261024): the signed record must say which pre-existing public symbols the
    # patch removed, and a PASS may remove none that the order did not explicitly allow.
    removed = record.get("removed_symbols")
    if not isinstance(removed, list):
        problems.append("The verification record does not include the removed-symbol regression check")
    elif removed:
        problems.append("Pre-existing code was removed (regression): " + ", ".join(map(str, removed))[:300])
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
    if verified.get("log_sha256") != hashlib.sha256(artifacts[LOG]).hexdigest():
        problems.append("The test log is not the one the verified run produced")
    if baseline.get("log_sha256") != hashlib.sha256(artifacts[BASELINE_LOG]).hexdigest():
        problems.append("The baseline log is not the one the baseline run produced")
    if not verified.get("reported") or not baseline.get("reported"):
        problems.append("The host-owned test runner did not report a result")
    if verified.get("exit_code") != 0:
        problems.append(f"The verified test run exited {verified.get('exit_code')!r}")
    outcomes, collected = verified.get("outcomes") or {}, verified.get("collected") or []
    tests = [t for t in collected if not str(t).startswith("collect::")]
    if not tests:
        problems.append("No tests ran")
    before = baseline.get("outcomes") or {}
    failing = sorted(t for t, o in outcomes.items() if not _passing(o, before.get(t)))
    if failing or any(t not in outcomes for t in tests):
        problems.append("Not every test passed: " + ", ".join(failing[:5] or [t for t in tests if t not in outcomes][:5]))
    lost = sorted(set(baseline.get("collected") or []) - set(collected))
    if lost:
        problems.append("Tests present at the base revision no longer run: " + ", ".join(lost[:5]))
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
