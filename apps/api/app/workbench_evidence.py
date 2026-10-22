"""Campus's own check that a Workbench PASS is backed by a real, passing test run of the exact patch.

The worker's verdict, mode and evidence strings are claims. A PASS is only kept when the result carries the
Workbench server's test-run record (qa-run.json), the complete test log it describes (qa-test.log) and the
patch (changes.diff), and Campus can confirm from those files that:

* the log is the complete one the record describes (size and sha256 match: nothing cut or swapped),
* unittest's own final summary in the log shows at least one test ran and ends with OK, with no failing
  test named anywhere in the log, and the record's exit code is 0 and its test count matches the log,
* the record was made for this exact patch (patch digest), with no untracked files present.

Campus never executes code, so it cannot re-run the tests. These checks stop truncated, inconsistent or
mismatched evidence and a bare PASS claim; they cannot stop a fully forged set of files from a compromised
worker host (that needs a Workbench-signed record, which is not implemented).
"""
import hashlib
import json
import re

PATCH, RECORD, LOG = "changes.diff", "qa-run.json", "qa-test.log"
TEST_COMMAND = re.compile(r"python3 -m unittest discover( -s tests -t \.)? -v")


def patch_digest(diff: bytes, untracked=()) -> str:
    """Matches the Workbench's team.patch_digest."""
    return hashlib.sha256(diff + b"\0" + "\n".join(untracked).encode("utf-8")).hexdigest()


def test_evidence_problems(artifacts: dict) -> list:
    """[] when the artifacts prove a passing test run of the exported patch, else the reasons they do not."""
    missing = [name for name in (PATCH, RECORD, LOG) if name not in artifacts]
    if missing:
        return ["PASS result is missing test evidence: " + ", ".join(missing)]
    try:
        record = json.loads(artifacts[RECORD].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return [f"{RECORD} is not valid JSON"]
    if not isinstance(record, dict):
        return [f"{RECORD} is not a test-run record"]
    log = artifacts[LOG]
    problems = []
    if len(log) != record.get("log_bytes") or hashlib.sha256(log).hexdigest() != record.get("log_sha256"):
        problems.append("The test log is not the complete log its record describes (truncated or changed)")
    if not isinstance(record.get("command"), str) or not TEST_COMMAND.fullmatch(record["command"]):
        problems.append(f"Unrecognised test command: {str(record.get('command'))[:80]!r}")
    if record.get("exit_code") != 0:
        problems.append(f"The test command exited {record.get('exit_code')!r}")
    text = log.decode("utf-8", "replace")
    counts = re.findall(r"^Ran (\d+) tests? in ", text, re.M)
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    last = lines[-1] if lines else ""
    if not counts:
        problems.append("The test log has no unittest summary, so there is no proof that tests ran")
    elif int(counts[-1]) < 1:
        problems.append("No tests ran")
    elif record.get("tests_ran") != int(counts[-1]):
        problems.append(f"The record says {record.get('tests_ran')!r} tests ran but the log says {counts[-1]}")
    if not re.fullmatch(r"OK( \(.*\))?", last):
        problems.append(f"The test log does not end with unittest's OK line (last line: {last[:80]!r})")
    failing = re.findall(r"^(?:FAIL|ERROR): .*$", text, re.M)
    if failing:
        problems.append("The test log names failing tests: " + "; ".join(failing[:5]))
    if record.get("untracked"):
        problems.append("Untracked files were present when the tests ran")
    if record.get("patch_sha256") != patch_digest(artifacts[PATCH]):
        problems.append("The tests were not run on this exact patch")
    return problems
