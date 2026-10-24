"""Test fixture: a Workbench verification record (ayven.verification/v2) exactly as workbench/src/verify.py writes
it, signed with the test attestation key. Tests alter `record` (via mutate) to show each Campus check refusing a
forged field."""
import hashlib
import hmac
import json

from apps.api.app.workbench_evidence import SCHEMA, acceptance_digest, canonical, key_id, request_key
from apps.api.app.workbench_scope import patch_paths

KEY = "attestation-key-for-tests-only-" + "x" * 20
PASS_LOG = "test_pricing.py ....                                     [100%]\n4 passed in 0.01s\n"
BASE_LOG = "test_pricing.py FFFF                                     [100%]\n4 failed in 0.02s\n"
REGRESSION_LOG = "test_pricing.py ....                                     [100%]\n4 passed in 0.01s (original tests)\n"
ACCEPTANCE_LOG = "test_owner.py ..                                         [100%]\n2 passed in 0.01s\n"
ACCEPTANCE_BASE_LOG = "test_owner.py FF                                         [100%]\n2 failed in 0.01s\n"
TESTS = ["test_pricing.py::test_%d" % i for i in range(4)]
OWNER_TESTS = ["test_owner.py::test_a", "test_owner.py::test_b"]
ACCEPTANCE = [{"path": "test_owner.py", "content": "def test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n"}]


def artifact(name, content):
    return {"name": name, "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}


def _run(text, collected, outs, code):
    raw = text.encode()
    return {"framework": "pytest", "command": "python3 -B /opt/ayven-verify/verify_runner.py pytest <report-fd>",
            "exit_code": code, "seconds": 0.1, "reported": True, "problem": "", "collected": list(collected),
            "outcomes": outs, "executed": {}, "log_sha256": hashlib.sha256(raw).hexdigest(), "log_bytes": len(raw)}


def logs_for(acceptance, log=PASS_LOG, base_log=BASE_LOG):
    return {"qa-test.log": log, "baseline-test.log": base_log, "regression-test.log": REGRESSION_LOG,
            "acceptance-test.log": ACCEPTANCE_LOG if acceptance else "",
            "acceptance-baseline-test.log": ACCEPTANCE_BASE_LOG if acceptance else ""}


def record_for(patch, order_id, allowed, revision=0, log=PASS_LOG, base_log=BASE_LOG, outcomes=None,
               acceptance=None, superseded=(), allowed_removals=()):
    diff = patch["content"].encode()
    named, _ = patch_paths(patch["content"])
    outcomes = outcomes or {t: "passed" for t in TESTS}
    logs = logs_for(acceptance, log, base_log)
    record = {
        "schema": SCHEMA, "goal": "g1", "task": "qa1", "order_key": request_key(order_id, revision),
        "base_revision": "a" * 40, "started_at": 1, "finished_at": 2, "runner_sha256": "r" * 64,
        "allowed_files": sorted(allowed), "verdict": "PASS", "reasons": [], "tamper_findings": [],
        "changed_files": [{"path": p, "status": "M"} for p in sorted(set(named))],
        "patch_sha256": hashlib.sha256(diff).hexdigest(), "patch_bytes": len(diff),
        "superseded_tests": sorted(superseded), "allowed_removals": sorted(allowed_removals),
        "acceptance_tests": acceptance_digest(acceptance), "removed_definitions": [], "signature_changes": [],
        "tests_modified": [], "tests_deleted": [],
        "baseline": _run(base_log, TESTS, {t: "failed" for t in TESTS}, 1),
        "verified": _run(log, TESTS, outcomes, 0 if all(o == "passed" for o in outcomes.values()) else 1),
        "regression": _run(REGRESSION_LOG, TESTS, {t: "passed" for t in TESTS}, 0),
        "acceptance": _run(ACCEPTANCE_LOG, OWNER_TESTS, {t: "passed" for t in OWNER_TESTS}, 0) if acceptance else None,
        "acceptance_baseline": _run(ACCEPTANCE_BASE_LOG, OWNER_TESTS, {t: "failed" for t in OWNER_TESTS}, 1)
        if acceptance else None,
        "coverage": {"level": "owner-acceptance" if acceptance else "regression-only", "baseline_tests": 4,
                     "regression_tests": 4, "acceptance_tests": 2 if acceptance else 0,
                     "acceptance_passing_at_base": 0, "worker_added_tests": [], "changed_lines": 3,
                     "changed_lines_executed_by_independent_tests": 3, "changed_lines_executed_by_worker_tests": 3,
                     "unexecuted_lines": {}, "notes": []},
    }
    contents = {"changes.diff": patch["content"], **logs}
    record["artifacts"] = {name: {"sha256": hashlib.sha256(text.encode()).hexdigest(), "bytes": len(text.encode())}
                           for name, text in contents.items()}
    return record, logs


def signed_evidence(patch, order_id, allowed, revision=0, mutate=None, key=KEY, signed=True, log=PASS_LOG,
                    base_log=BASE_LOG, outcomes=None, acceptance=None, superseded=(), allowed_removals=()):
    """[qa-run.json and the five test logs] artifacts (the caller adds changes.diff)."""
    record, logs = record_for(patch, order_id, allowed, revision, log, base_log, outcomes, acceptance, superseded,
                              allowed_removals)
    if mutate:
        mutate(record)
    signature = None
    if signed:
        k = key.encode()
        signature = {"alg": "HMAC-SHA256", "key_id": key_id(k),
                     "value": hmac.new(k, canonical(record), hashlib.sha256).hexdigest()}
    envelope = json.dumps({"record": record, "signature": signature}, indent=1, sort_keys=True)
    return [artifact("qa-run.json", envelope)] + [artifact(name, text) for name, text in logs.items()]
