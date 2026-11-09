# Release hardening — Claude Code blockers (25 Oct 2026)

Branch `security/release-hardening-20261024`, on top of the integrated candidate (Milo `c2ba031`, Campus
`056e52b`). **Not merged.** Exact commits for Q&T are at the end.

This closes the Claude Code blockers from the release NO-GO: the sandbox-to-host write vulnerability, complete
failure logs, trustworthy REAL/SIMULATED provenance (D12), and the investigation of the Campus API failures.

## 1. Sandbox-to-host write — eliminated and proved
**The hole:** when the Workbench server runs as root (the laptop runs the worker under WSL as root), bubblewrap left
the jailed process holding every namespaced capability — including `CAP_SYS_ADMIN`. Jailed code could `mount` a
tmpfs or the host disk and write through it, and `mknod` a block device, escaping the read-only bind. Proven on the
box: before the fix a jailed `mount -t tmpfs` returned `MOUNTED`.

**Fix (Milo `workbench/src/team.py`, `sandbox_argv`):** `--cap-drop ALL`. The jail now runs with `CapEff: 0`,
and `mount` and `mknod` both fail. Also hardened: a patch that adds a symlink or submodule is refused before any
verification tree is built (so the host-side `removed_symbols` read cannot be pointed outside the tree), and the
agent file tools refuse `.git` in any letter case anywhere in a path.

**Adversarial proof — `workbench/src/tests/test_release_hardening.py::SandboxIsolation` (real bubblewrap, run as
root on the WSL box):** every test runs a hostile command in the jail and then checks the HOST:
- writes to `/usr`, `/etc`, `/home`, the parent of the workspace, and an absolute host path — none land;
- `CapEff` is `0000000000000000`; `mount` and `mknod` both refused;
- a planted symlink to a host secret does not overwrite it, and the host-side reader refuses to follow it;
- no network; the only writable bind is the workspace.

## 2. Complete failure logs — kept and exportable
**Before:** a failed job returned only the QA defect strings; the model's turns, tool commands and exit codes were
left on the Workbench host and never reached the owner.

**Fix:**
- Milo `server.py`: a read-only machine route `GET /api/logs?id=<goal>` returns every log the goal produced — each
  task's turns and tool results (exit codes included), every verification run's log, timeout notes and the
  model-call ledger — each with its byte size and sha256. A log over 200 000 bytes is sent as its last 200 000
  bytes with `complete=false` and the full size and hash, so nothing is silently cut (the full file stays on the
  host). Redacted exactly like the panel.
- Milo `campus_worker.py` `failure_export`: a FAIL or BLOCKED result now attaches the verification artifacts (even
  for a BLOCKED verification) plus the task/verification logs and a `logs-manifest.json` naming every log kept
  (size, sha256, whether it was sent whole and whether it was attached).
- Campus `workbench_bridge.py`: the worker's `log-*` artifacts and `logs-manifest.json` are stored and previewable
  (`GET /milo/executions/{id}/preview/<name>`) in the failed state; arbitrary names are still refused.

**Proof:** `test_release_hardening.py::FailureLogs` (host side) and Campus
`tests/test_release_provenance_and_logs.py::FailureLogs` (a BLOCKED turn-limit job: the dev log with its exit code
and the manifest are previewable; other names 404).

## 3. REAL / SIMULATED provenance — trustworthy and independently verifiable (D12)
**Before (D12):** the worker hard-coded `execution_mode="REAL"` for every PASS, even against a stub endpoint, so
Campus could show a stub run as REAL.

**Fix — the label is derived, never taken from the worker:**
- Milo `provenance.py` + `team.py`: the Workbench server records every model call the agent made for a goal
  (`runs/<goal>/model-calls.jsonl`, outside the jail): the model the endpoint said answered and its
  `system_fingerprint`. At verification time it also probes the configured endpoint's Ollama identity
  (`/api/version` and the model's sha256 digest from `/api/tags`). `verify.py` binds this summary into the
  **signed** verification record as `model_provenance`. `kind` is `ollama` only when the endpoint is a real Ollama
  serving the configured model by digest **and** every recorded call named that model with Ollama's fingerprint;
  otherwise `unverified`, with the reasons.
- Campus `workbench_evidence.py` + `workbench_bridge.py`: Campus reads `execution_mode` from the **signed** record's
  provenance (`provenance_mode`), never from the worker's field. `unverified` (or absent) provenance → SIMULATED,
  which is recorded and shown but can never wait for or receive approval. A worker may still claim *less* (a
  SIMULATED sample stays SIMULATED). The worker's original label, when it disagreed, is kept as
  `worker_execution_mode`.

**What this proves and does not:** it binds a REAL result to a local Ollama that served this model digest and
answered every call; it cannot prove that server is not an imitation built to pass these checks. Q&T cross-check:
`ollama ps`/`ollama list` on the host during the run shows the model loaded and its digest; the recorded call count
and tokens match the logs.

**Independent proof:**
- Live, against the box's Ollama: `test_release_hardening.py::ModelProvenance::test_a_live_ollama_endpoint_is_verified`
  (run with `AYVEN_PROVENANCE_LIVE=1`) probes the real endpoint and asserts `kind=ollama` with a 64-hex digest.
- Negative: no recorded calls, a non-Ollama fingerprint, a different served model, and any provenance with problems
  all resolve to SIMULATED.
- End to end in Campus: `tests/test_release_provenance_and_logs.py::Provenance` — a verified Ollama record reaches
  `awaiting_approval` as REAL; a stub-endpoint provenance with the worker claiming REAL is stored SIMULATED and
  refused for acceptance; a REAL claim with no signed record is downgraded to NONE.

**Scripted-model drills:** a drill that uses the scripted stand-in has no real Ollama, so its provenance is
`unverified`. `AYVEN_TEST_ACCEPT_UNVERIFIED_MODEL=1` **with `AYVEN_ENV=test`** lets such a result be treated as
REAL for the drill, and every such result is marked `test_override` and keeps the true `unverified` provenance on
the record. Campus and the companion refuse that switch to start outside the test environment.

## 4. Campus API failures — investigated; unrelated to the candidate
The full Campus suite (`apps/api/tests` + `tests`) was run on the unmodified release base `056e52b` and on this
branch, each in its own fresh tree:
- base `056e52b`: 19 failed / 546 passed / 4 skipped / 1 error;
- this branch: 22 failed / 587 passed / 4 skipped / 1 error (587 vs 546 passing: the new provenance and log tests).

The only ids that differed are all in `apps/api/tests/test_qa_campus_faults.py`. That file is **flaky on the
untouched release itself**: re-running it three times on base `056e52b` gave 10, 10 and 8 failures; three times on
this branch gave 9, 3 and 6. The set changes run to run in both trees, so the differences are timing/concurrency
noise in that one HTTP-job fault file, not a regression from this branch — this branch, if anything, failed fewer.
Evidence: `docs/release/evidence/claude-code-hardening/qa-campus-faults-flakiness.txt`.

The steady failures (present identically on base and branch) are environment, not code: optional packages absent on
the box and the one pre-existing collection error. None touches the hardened Workbench lane
(`workbench_bridge`, `workbench_evidence`, `workbench_scope`), whose own suites are fully green (below).

## 5. Pre-existing release bugs fixed along the way
- `team.request_info` crashed (`NoneType / str`) when `_home` was unset, so the release's own D1 apply check
  (`reviewed_patch_check`) failed the `checks` core suite. Guarded.
- The `checks` core test `test_apply_includes_new_files_without_publishing` still called `apply_changes` without
  the reviewed SHA the release's D1 now requires. Updated to the D1 contract (supply the reviewed digest and a
  passing verification record), so the test proves the new fail-closed behaviour instead of the old.

## Security review status
Reviewed and reported separately in `docs/release/SECURITY-REVIEW.md` (same branch): the hardened execution lane,
the P&D `desktop/` companion and specialists, `campus_ext/specialist_tasks.py`, and the Milo owner-decision/apply
and specialist proxy routes.

## Test results (WSL Ubuntu 24.04, bwrap 0.9, Python 3.12; this branch)
- Workbench: core `checks` 8/8; discovered tests **196 OK, 2 skipped**; proxy 3/3.
- `test_release_hardening.py`: 13 OK (1 live-Ollama case skipped in WSL; it passes from Windows where Ollama is
  reachable — 5/5 `ModelProvenance` including the live digest check).
- Campus `tests/` (the Workbench-lane and release suites): **173 passed, 1 skipped**, including the new
  `test_release_provenance_and_logs.py`.
- Campus full suite: as in §4.

## Exact commits and how to reproduce
- Milo `security/release-hardening-20261024` @ `c3d56dc`
- Campus `security/release-hardening-20261024` @ `6b4aa5a`
```
# Workbench (Milo checkout)
cd workbench/src && python3 -m unittest checks && python3 -m unittest discover -s tests
python3 -m unittest -v tests.test_release_hardening
AYVEN_PROVENANCE_LIVE=1 AYVEN_PROVENANCE_MODEL=<model> python3 -m unittest tests.test_release_hardening.ModelProvenance
# Campus
AYVEN_LLM_STUB=1 PYTHONPATH=.:apps/api python -m pytest -q tests/
# End-to-end real model (Ollama at :11434), proving provenance reaches Campus as REAL:
cd workbench/tests/e2e && python real_model_run.py <campus> <milo>/workbench/src out/ <model>
```
