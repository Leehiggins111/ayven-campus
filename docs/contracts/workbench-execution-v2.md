# Campus ⇄ Workbench execution contract (v2, 24 Oct 2026)

This supersedes `workbench-execution-v1.md`. It covers one coding job, from Milo through Campus to the Workbench
and back to the owner: how it is verified, how the owner decides on **one exact version**, and how only that
version is ever applied.

**What changed from v1:**
- Verification is now `ayven.verification/v2`. The original tests are re-run against the patch, owner acceptance
  tests are run, removed definitions are checked, and the record carries an honest coverage summary.
- New routes: review packages; decisions bound to a package hash; the signed approval; the apply queue; and the
  clarification route Milo already calls.
- New status fields: times, the brief, the decision reason, coverage and apply state.
- **One breaking change for Milo:** `POST …/decision` now **requires** `package_sha256` and `revision`.

**Where it lives:**
- Campus: `apps/api/app/workbench_bridge.py`, `workbench_evidence.py`, `workbench_scope.py`.
- Workbench (repo `Leehiggins111/Milo`): `workbench/src/verify.py`, `verify_runner.py`, `approved_apply.py`,
  `campus_worker.py`, `server.py`.

**Branches:** Campus and Milo, both `integration/execution-safety-20261024`. Commits are listed in the handoff
`workbench/docs/agent-handoffs/execution-safety-20261024.md`.

## Principals
| Principal | Credential | May call |
|---|---|---|
| Owner (Milo server, Campus UI) | `AYVEN_API_TOKEN` bearer or the Campus session cookie. The read-only `AYVEN_READ_TOKEN` gets GETs only. | `/milo/executions…` |
| Pull worker (`campus_worker.py`) | `AYVEN_WORKER_TOKEN` bearer (32+ characters, and **never equal to the owner key**: if it is, it is refused) | `/internal/workbench/…` only |
| Workbench server and Campus | `AYVEN_ATTESTATION_KEY` (32+ characters), held only by these two | Workbench signs verification records; Campus signs approvals |

- The worker can carry records and approvals but can make or change neither.
- Milo passes the signed-in owner's address as `X-Ayven-Owner` on decision calls. Campus records it in the decision
  and in the approval.

## States
```
queued ──claim──> running ──result──> awaiting_approval ──accept(package)──> completed ──apply──> [apply queue]
  ^                 │  │                    │                                              requested → claimed →
  │  lease expiry ──┘  ├─ FAIL/BLOCKED ──> failed ──────┐                                  applied | refused | failed
  │  (attempts < 3)    ├─ CLARIFY ──────> needs_clarification ── answer ──┐
  │                    └─ limit ────────> failed          awaiting_approval ──reject(package)──> rejected
  └────────── revise / answer (new revision, new Workbench goal, new package) ───────────────────────┘
cancel: queued | running | needs_clarification → cancelled
```
- **Accepting is never automatic, and neither is applying.** These are two separate owner actions.
- A decision is final. The same decision on the same package returns `duplicate: true`. A different decision gets
  409.
- A rejected result can never be applied: the apply route returns 409 and nothing is queued.
- Rework or an answer creates a new revision. That revision gets a new request key, a new Workbench goal and a new
  package. A package from an earlier revision can no longer be decided on.

## Owner endpoints (all under the owner credential)
| Method and path | Body | Result | Errors |
|---|---|---|---|
| `POST /milo/executions` | `Idempotency-Key` header; body below | `{order_id, project_id, duplicate, state, mode}` | 400 invalid; 409 same key with a different body |
| `GET /milo/executions?limit=1..100` | | `{executions:[{order_id, state, revision, question, verification, verdict, execution_mode, scope_verified, tests_verified, defects, artifacts, attempts, created_at, updated_at, finished_at, worker_id, last_heartbeat_at, acceptance_criteria, constraints, job_cap_gbp, review, decision, apply, allowed_files, objective, project_id}]}` | |
| `GET /milo/executions/{id}` | | the full order view (the fields below) | 404 |
| `GET /milo/executions/{id}/events?after=SEQ&limit≤500` | | `{events:[{seq, ts, revision, kind, actor, text}]}` | 404 |
| `GET /milo/executions/{id}/review` | | `{order_id, state, revision, package_sha256, package, intact, decision, apply}` | 404; 409 when there is no verified result |
| `POST /milo/executions/{id}/decision` | `{decision:"accept"\|"reject", reason, package_sha256, revision, acknowledge_coverage?}` | `{state, decision, reason, approval_id, package_sha256, duplicate}` | 400 when `package_sha256` or `revision` is missing, or a reject has no reason; 409 for a stale revision, a different package, artifacts that changed in storage, a state other than awaiting_approval, a decision already made, or worker-tests-only without acknowledgement; 503 when Campus has no attestation key |
| `POST /milo/executions/{id}/apply` | `{package_sha256}` | `{apply_state, duplicate, outcome?}` | 409 if rejected ("A rejected result is never applied"), not accepted, a different package, or artifacts no longer intact |
| `GET /milo/executions/{id}/apply` | | `{order_id, apply:{state, attempts, requested_at, updated_at, approval_id, package_sha256, outcome:{outcome, commit, detail, worker}}}` | 404 |
| `POST /milo/executions/{id}/clarification` | `{answer}` (Milo D1) | `{state:"queued", revision, duplicate}` | 400 empty answer; 409 not waiting for an answer |
| `POST /milo/executions/{id}/revise` | `{kind:"rework"\|"clarification", text}` with an optional `Idempotency-Key` | `{state:"queued", revision, duplicate}` | 409 wrong state; 409 same key with a different body |
| `POST /milo/executions/{id}/cancel` | `{reason?}` | `{state:"cancelled", duplicate}` | 409 once a result exists |
| `GET /milo/executions/{id}/preview/{name}` | name: `changes.diff`, `qa-run.json`, `qa-test.log`, `baseline-test.log`, `regression-test.log`, `acceptance-test.log`, `acceptance-baseline-test.log` | text/plain; headers `X-Ayven-Verification`, `X-Ayven-Outcome`, `X-Content-SHA256` | 409 before a final result |
| `GET /milo/executions/{id}/artifacts/{name}` | | octet-stream with `X-Content-SHA256`; **only after acceptance** | 409 before acceptance |

**Order view: the fields added in v2.**
- The brief (Milo D4): `acceptance_criteria`, `constraints`, `job_cap_gbp`.
- Times and liveness (Milo D3): `worker_id`, `claimed_at`, `last_heartbeat_at`, `lease_until`, `finished_at`, `updated_at`. All are Unix seconds.
- Owner tests and exemptions: `acceptance_tests` (paths only; their content is never echoed), `superseded_tests`,
  `allowed_removals`.
- `review`: `{package_sha256, coverage}`, or `null`.
- `decision` (Milo D5; the reason is stored): `{decision, reason, owner:{principal, owner}, decided_at,
  package_sha256, revision, approval_id, acknowledged_coverage}`.
- `apply`: as in `GET …/apply`.

### Submission body
```json
{"objective": "Add multiply(a, b) to calc.py", "project_alias": "cloud-demo",
 "acceptance_criteria": ["add() keeps working", "multiply(4, 3) == 12"], "constraints": [],
 "job_cap_gbp": 0, "allowed_files": ["calc.py", "test_calc.py"],
 "max_minutes": 30, "token_cap": 20000, "allow_clarification": false,
 "acceptance_tests": [{"path": "tests_acceptance/test_owner_calc.py", "content": "import unittest\n..."}],
 "superseded_tests": [], "allowed_removals": []}
```
- **`acceptance_tests`** are optional, written by the owner (or by Milo for the owner), and never shown to the worker.
  - At most 10 files and 32,000 bytes in total.
  - Each must be a Python test file (`test_*.py`, `*_test.py` or under `tests/`), and not `conftest.py`.
  - None may be on `allowed_files`, and none may overwrite a project file.
- **`superseded_tests`** are original test ids the change may retire. **`allowed_removals`** are public definitions it
  may remove, written `path::name` (for example `calc.py::add`). Both default to empty, so nothing is exempt.

## The review package (what the owner decides on)
It is frozen when Campus accepts a verified PASS. Its `package_sha256` is sha256 over the canonical JSON: sorted
keys, `,`/`:` separators, ASCII. Fields:

- **Identity:** `schema: "ayven.review-package/v1"`, `order_id`, `revision`, `request_key`, `project_alias`.
- **The brief:** `objective`, `acceptance_criteria`, `allowed_files`.
- **The patch:** `base_revision` (the exact commit), `patch_sha256`, `patch_bytes`, `changed_files`, `execution_mode`.
- **The files:** `artifacts` maps each name to `{sha256, bytes}`, covering every file stored for this result.
- **The verification:** `{schema, verdict, record_sha256, key_id}`.
- **Tests and definitions:** `acceptance_tests` (path and sha256 for each), `superseded_tests`, `allowed_removals`,
  `removed_definitions`, `signature_changes`, `tests_modified`.
- **`coverage`**: see Verification v2 below.

`intact` on `GET …/review` re-hashes every stored artifact against the package. If anything changed in storage, it
is false and every decision and apply is refused.

## The signed approval (made by Campus on accept)
`decision_json.approval = {"record": {...}, "signature": {"alg": "HMAC-SHA256", "key_id", "value"}}`. The record:
- `schema: "ayven.approval/v1"`, a random `approval_id` (32 hex characters) and `decision: "accept"`;
- `order_id`, `revision`, `request_key`, `package_sha256`;
- `patch_sha256`, `base_revision`, `allowed_files`, `project_alias`;
- `owner: {principal, owner}` and `decided_at`.

Workbench applies nothing without a valid approval.

## Worker endpoints (worker token)
- `POST /internal/workbench/claim`, `…/{id}/heartbeat` and `…/{id}/result` are as in v1. The claimed job also
  carries `acceptance_tests`, `superseded_tests` and `allowed_removals`, which the worker forwards to the Workbench
  server only. A PASS must carry `qa-run.json` and the six artifacts of verification v2.
- `POST /internal/workbench/apply/claim {worker_id}` returns `{apply: null}` or
  `{apply: {order_id, revision, fence, lease_seconds, project_alias, approval, package_sha256, patch, record}}`.
  - It is leased and fenced like orders. A lease that expires re-queues the apply. After 3 attempts it is `failed`,
    and the owner may request it again.
  - Campus refuses to hand out an apply, and marks it `refused`, if the stored artifacts no longer match the accepted
    package.
- `POST /internal/workbench/apply/{id}/result {worker_id, fence, outcome:"applied"|"refused", commit, detail}`.
  - `applied` requires the 40-character commit.
  - A stale fence or an expired lease gets 409. An identical replay returns `duplicate: true`. A different result
    after the apply has finished gets 409.

## Verification v2 (what makes `tests_verified` true)
Workbench's `verify.py` builds these trees with `git archive`, never from the agent's workspace:

| Tree | What it is |
|---|---|
| BASE | the recorded base revision |
| PATCHED | BASE plus the exact patch |
| REGRESSION | PATCHED with every test file and `conftest.py`/`pytest.ini`/`tox.ini` put back exactly as at BASE, and tests the patch added removed. The exception: a base test file whose every test the owner superseded keeps the patched version. |
| ACCEPTANCE | REGRESSION plus the owner tests |
| ACCEPTANCE-BASE | BASE plus the owner tests |

Each tree runs in bubblewrap through the host-owned runner. The runner reports per-test outcomes over a
nonce-authenticated pipe, and also which changed lines executed (stdlib tracing).

**PASS requires all of the following:**
1. The patched run passes. Every base test still exists unless superseded. A skip or xfail counts only if it was
   the same at BASE.
2. Every original test passes against the patched code (REGRESSION).
3. Every owner acceptance test passes (ACCEPTANCE).
4. No public function, class or method that existed at BASE is gone, unless it is in `allowed_removals`. A name
   re-exported by import counts as kept.
5. There are no tamper findings. Any change to `conftest.py`, `sitecustomize.py`, `usercustomize.py` or a `*.pth`
   file counts as interference whoever allowed it, as do the known runner-forging patterns.
6. The patch stays within the allowed files and is no larger than 100 KB.

**What Campus checks** (`workbench_evidence.test_evidence_problems`):
- the signature and key id;
- the schema;
- the order key for this order and revision;
- the allow-list, the acceptance-test digests, `superseded_tests` and `allowed_removals`, each equal to the order's;
- every artifact's sha256 and size;
- every run's log digest; a log with content but no matching run is refused;
- every rule above, from the record itself.

**`coverage`** records what was and was not checked:
- **`level`**:
  - `owner-acceptance`: owner tests ran and passed;
  - `regression-only`: original tests exist, but the new behaviour is checked only by the worker's tests;
  - `worker-tests-only`: no original or owner tests at all. Accepting this needs `acknowledge_coverage: true`.
- **Counts:** `baseline_tests`, `regression_tests`, `acceptance_tests`, `acceptance_passing_at_base`.
- **Worker tests and changed lines:** `worker_added_tests`, `changed_lines`,
  `changed_lines_executed_by_independent_tests`, `changed_lines_executed_by_worker_tests`, `unexecuted_lines`.
- **`notes`**: plain-language caveats.

A PASS is never proof of correctness beyond these checks. Milo should show `coverage.notes` next to the verdict.

## What Workbench checks before applying (`approved_apply.apply_approved`)
1. The approval's signature, schema `ayven.approval/v1`, and `decision == accept`.
2. The verification record's signature, its schema, and that it is a PASS.
3. sha256(patch) equals the approval's `patch_sha256`, which equals the record's `patch_sha256`. The approval and
   the record also agree on `base_revision`, `request_key` and `allowed_files`.
4. The record is byte-for-byte (in canonical form) Workbench's **own stored** record for that order revision. The
   project is the one recorded for that goal; nothing from the worker chooses it.
5. Every patch path is a plain, allowed file. Symlinks, submodules, `..`, and `.git` in any letter case are refused.
6. The project is clean, untracked files included, and its HEAD is exactly the reviewed `base_revision`.
7. The commit is built in a temporary index with `git apply --cached` from the patch bytes, and the changed paths are
   re-checked. The project then moves by `merge --ff-only`, with hooks off.
8. The ledger in `<home>/applied/` (`approval-<id>.json`, `order-<request_key>.json`) records each apply:
   - a repeated request returns `duplicate`;
   - a second approval for the same revision is refused;
   - after a crash, the apply resumes to the same commit and never applies a second time.

The Workbench panel's own **Apply** button follows the same rule. The page sends back the sha256 of the patch the
owner viewed; only those bytes are applied, never the workspace as it is now. Campus orders cannot be applied from
the panel at all.

## Security invariants (each enforced, with the test that proves it)
| # | Invariant | Enforced in | Test |
|---|---|---|---|
| I1 | No PASS without a Workbench-signed v2 record for this order revision | `workbench_evidence` | `test_workbench_evidence_http`, `test_workbench_approval_http` |
| I2 | A patch that breaks or deletes existing behaviour fails, even when it edits the tests | `verify.py` (REGRESSION, API check) | `test_execution_safety.NoFalsePass` |
| I3 | Owner acceptance tests are invisible to the worker and bound by digest | `team.submit_goal` (request.json outside the jail), `workbench_evidence` | `test_execution_safety`, `test_workbench_approval_http` |
| I4 | A decision names one package and revision; a stale or changed one is refused | `owner_decision` | `test_workbench_approval_http` |
| I5 | Only a Campus-signed approval for the exact patch, base, files and order can be applied | `approved_apply` | `test_execution_safety.ExactApply` |
| I6 | Applying never reads the workspace; it uses the reviewed bytes only, at most once | `approved_apply`, `server.apply_changes` | `ExactApply`, `checks.CoreChecks` |
| I7 | A rejected result is never applied | `request_apply`, `apply_approved` | both suites |
| I8 | `.git` in any letter case, traversal, symlinks and submodules never pass the scope checks | `allowlist.check_path`, `workbench_scope.check_path` | both suites |
| I9 | The worker token cannot act as owner, and the owner key cannot serve as the worker token | `access.boundary`, `worker_authorized` | `test_workbench_approval_http`, `test_workbench_lifecycle_http` |
| I10 | Failed and blocked runs keep every log, exit code and the exact patch | `campus_worker.failure_export`, previews in the failed state | drills |

## Residual risks
- **Test runner forgery.** Code under test runs in the same process as the runner, so deliberate introspection
  could forge the runner's report. Two things contain this:
  - the tamper scan and the runner-hook ban;
  - the REGRESSION and ACCEPTANCE runs. Forging only the patched run is no longer enough; a forger must defeat
    every run, using code that passes the tamper scan.

  The owner still previews the exact patch.
- **Shared signing key.** The attestation key is symmetric and shared by the Workbench host and Campus.
  - A fully compromised Workbench host can sign a fake record, and it can also change the project directly.
  - A compromised Campus can sign an approval.

  An asymmetric key would not change which host must be trusted for what. It would remove Campus's ability to forge
  verification records and Workbench's ability to forge approvals; that is a possible later improvement.
- **What a PASS does not show.** A changed line that no independent test executes is reported, not failed.
  `coverage.level` and `notes` show this to the owner.
