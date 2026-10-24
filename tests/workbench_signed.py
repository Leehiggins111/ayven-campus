"""Test fixture: a Workbench verification record exactly as workbench/src/verify.py writes it, signed with the
test attestation key. Tests alter `record` (via mutate) to show each Campus check refusing a forged field."""
import hashlib
import hmac
import json

from apps.api.app.workbench_evidence import SCHEMA, canonical, key_id, request_key
from apps.api.app.workbench_scope import patch_paths

KEY = "attestation-key-for-tests-only-" + "x" * 20
PASS_LOG = "test_pricing.py ....                                     [100%]\n4 passed in 0.01s\n"
BASE_LOG = "test_pricing.py FFFF                                     [100%]\n4 failed in 0.02s\n"
TESTS = ["test_pricing.py::test_%d" % i for i in range(4)]


def artifact(name, content):
    return {"name": name, "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}


def record_for(patch, order_id, allowed, revision=0, log=PASS_LOG, base_log=BASE_LOG, outcomes=None):
    diff = patch["content"].encode()
    named, _ = patch_paths(patch["content"])

    def run(text, outs, code):
        raw = text.encode()
        return {"framework": "pytest", "command": "python3 -B /opt/ayven-verify/verify_runner.py pytest <report-fd>",
                "exit_code": code, "seconds": 0.1, "reported": True, "problem": "", "collected": list(TESTS),
                "outcomes": outs, "log_sha256": hashlib.sha256(raw).hexdigest(), "log_bytes": len(raw)}
    outcomes = outcomes or {t: "passed" for t in TESTS}
    return {"schema": SCHEMA, "goal": "g1", "task": "qa1", "order_key": request_key(order_id, revision),
            "base_revision": "a" * 40, "started_at": 1, "finished_at": 2, "runner_sha256": "r" * 64,
            "allowed_files": sorted(allowed), "verdict": "PASS", "reasons": [], "tamper_findings": [], "removed_symbols": [],
            "changed_files": [{"path": p, "status": "M"} for p in sorted(set(named))],
            "patch_sha256": hashlib.sha256(diff).hexdigest(), "patch_bytes": len(diff),
            "baseline": run(base_log, {t: "failed" for t in TESTS}, 1),
            "verified": run(log, outcomes, 0 if all(o == "passed" for o in outcomes.values()) else 1),
            "artifacts": {name: {"sha256": hashlib.sha256(text.encode()).hexdigest(), "bytes": len(text.encode())}
                          for name, text in (("changes.diff", patch["content"]), ("qa-test.log", log),
                                             ("baseline-test.log", base_log))}}


def signed_evidence(patch, order_id, allowed, revision=0, mutate=None, key=KEY, signed=True, log=PASS_LOG,
                    base_log=BASE_LOG, outcomes=None):
    """[qa-run.json, qa-test.log, baseline-test.log] artifacts (the caller adds changes.diff)."""
    record = record_for(patch, order_id, allowed, revision, log, base_log, outcomes)
    if mutate:
        mutate(record)
    signature = None
    if signed:
        k = key.encode()
        signature = {"alg": "HMAC-SHA256", "key_id": key_id(k),
                     "value": hmac.new(k, canonical(record), hashlib.sha256).hexdigest()}
    envelope = json.dumps({"record": record, "signature": signature}, indent=1, sort_keys=True)
    return [artifact("qa-run.json", envelope), artifact("qa-test.log", log), artifact("baseline-test.log", base_log)]
