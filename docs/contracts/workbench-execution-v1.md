# Campus ⇄ Workbench execution contract (v1, 23 Oct 2026)

> **Superseded by `workbench-execution-v2.md` (24 Oct 2026)**: verification v2, review packages, signed approvals, apply queue. The decision route now requires `package_sha256` and `revision`.

This is the agreed API for one coding job, from Milo through Campus to the Workbench and back to the owner. It
extends the endpoints Milo already calls (`integration/milo-work-submission-recovery-20261023`). Nothing in it
removes or renames those endpoints.

- Implementation: `apps/api/app/workbench_bridge.py` and `apps/api/app/workbench_evidence.py`.
- Workbench side: `workbench/src/campus_worker.py`, `server.py`, `team.py` and `verify.py` in Leehiggins111/Milo.

## Principals
| Principal | Credential | May call |
|---|---|---|
| Owner (Milo server, Campus UI) | `AYVEN_API_TOKEN` bearer, or the Campus session cookie | `/milo/executions…` |
| Pull worker (`campus_worker.py`) | `AYVEN_WORKER_TOKEN` bearer (32+ characters) | `/internal/workbench/…` only |
| Workbench server | `AYVEN_ATTESTATION_KEY` (32+ characters), shared with Campus only | signs verification records |

- The worker token cannot reach owner routes (401), and the owner token cannot reach worker routes (401).
- The worker never holds the attestation key, so holding the worker token is not enough to forge a passing result.

## States and who may change them
```
                 owner cancel                     owner cancel
   ┌────────────────────────────┐      ┌─────────────────────────┐
   v                            │      │                         │
cancelled <── queued ──claim──> running ──result──> awaiting_approval ──accept──> completed
               ^  ^               │  │                     │
               │  └─lease expiry──┘  ├─result FAIL/BLOCKED─> failed ─────────┐
               │   (attempts < 3)    ├─limit/time/budget──> failed           │ owner revise (rework)
               │                     ├─result CLARIFY─────> needs_clarification ─┐
               │                     └─result PASS (SIMULATED)─> simulated (record only)
               │                                          awaiting_approval ──reject──> rejected ─┘
               └──────────── owner revise (new revision, new Workbench goal) ─────────────┘
```
- **Owner:** create, cancel (from queued, running or needs_clarification), revise (rework from failed or
  rejected; clarification from needs_clarification), accept or reject (from awaiting_approval only).
- **Worker:** claim, heartbeat, and result. It can act only with the **current fence of a live lease**;
  anything else gets 409.
- **Campus:** handles lease expiry (re-queues; fails the order after 3 attempts per revision) and the
  `max_minutes` / `token_cap` / `job_cap_gbp` limits (the order fails, and the next heartbeat gets 409).
- Terminal states are `completed`, `cancelled` and `simulated`; `failed` and `rejected` are terminal until the owner
  revises.
- **Accepting is never automatic.** It requires `verdict=PASS`, `execution_mode=REAL`, `scope_verified` and
  `tests_verified`.

## Owner endpoints
| Method and path | Body / query | Result |
|---|---|---|
| `POST /milo/executions` | `Idempotency-Key` header (≤200 chars); body below | `{order_id, project_id, duplicate, state, mode}` |
| `GET /milo/executions?limit=1..100` | | `{executions:[{order_id, state, revision, question, verification, verdict, execution_mode, scope_verified, tests_verified, defects, artifacts, attempts, created_at, allowed_files, objective, project_id}]}` |
| `GET /milo/executions/{id}` | | the order view: `state, revision, revisions[], question, usage{tokens,cost_gbp}, limits{max_minutes,token_cap,job_cap_gbp}, verification VERIFIED/UNVERIFIED, result{…}, owner_accepted, attempts` |
| `GET /milo/executions/{id}/events?after=SEQ&limit≤500` | | `{events:[{seq, ts, revision, kind, actor, text}]}`; kinds: created, claimed, progress, lease_expired, result, failed, cancelled, revised, decision |
| `POST /milo/executions/{id}/cancel` | `{reason?}` | `{state:"cancelled", duplicate}`; 409 once a result exists |
| `POST /milo/executions/{id}/revise` | `{kind:"rework"\|"clarification", text}` plus an optional `Idempotency-Key` | `{state:"queued", revision, duplicate}` |
| `POST /milo/executions/{id}/decision` | `{decision:"accept"\|"reject", reason}` (a reason is required to reject) | `{state}` |
| `GET /milo/executions/{id}/preview/{name}` | name: `changes.diff`, `qa-run.json`, `qa-test.log`, `baseline-test.log` | text/plain; headers `X-Ayven-Verification`, `X-Ayven-Outcome`, `X-Content-SHA256`; available in awaiting_approval, completed, rejected, failed and simulated, **before acceptance** |
| `GET /milo/executions/{id}/artifacts/{name}` | | octet-stream plus `X-Content-SHA256`; **only after acceptance** |

### Submission body (`POST /milo/executions`)
```json
{"objective": "…task text…", "project_alias": "cloud-demo",
 "acceptance_criteria": ["All 8 tests in test_pricing.py pass"], "constraints": [],
 "job_cap_gbp": 0, "allowed_files": ["pricing.py"],
 "max_minutes": 30, "token_cap": 20000, "allow_clarification": false}
```
- `allowed_files` must be exact relative paths (no globs, no `..`, no `.git*`, no `.github/`). There is no default.
- `max_minutes` is optional (default 30, range 1–240). `token_cap` is optional (500 to 5,000,000 model
  tokens). `allow_clarification` defaults to false.
- **Idempotency.** The same key with the same body returns the same order (`duplicate: true`); this holds under
  concurrent requests too, and the regression test fires eight at once. The same key with a different body
  gets 409. A lost reply is always safe to retry with the same key.

## Worker endpoints (pull, worker token)
- `POST /internal/workbench/claim {worker_id}` returns `{job: null}` or a job:
  `{order_id, fence, lease_seconds, revision, revisions[], request_key, objective, acceptance_criteria,
  constraints, allowed_files, max_minutes, deadline, token_cap, allow_clarification, budget_gbp}`.
- `request_key` is sha256(order_id) for revision 0 and sha256(`order_id:rN`) after that. The worker passes it
  to Workbench as `idempotency_key`, so **one Workbench goal per revision**. A re-claim after a crash or lease
  takeover re-attaches to that same goal instead of starting a second execution.
- `POST /internal/workbench/{id}/heartbeat {worker_id, fence, progress{stage, status, tokens, cost_gbp}}`
  renews the lease and records changes of stage as `progress` events.
  - **409** means stop: the order was cancelled, the lease is stale, or a time or budget limit was reached.
    The worker then calls Workbench `POST /api/campus-stop {id, request_key}` and walks away.
- `POST /internal/workbench/{id}/result {worker_id, fence, verdict, execution_mode, evidence[], defects[],
  artifacts[{name, content, sha256}], question?, usage?}`.
  - `verdict` is PASS, FAIL, BLOCKED or CLARIFY. A PASS must say REAL or SIMULATED. FAIL and BLOCKED may say
    NONE when nothing ran. CLARIFY must say NONE and carry a question.
  - At most 10 artifacts of 256 KiB each, with hashes checked.
  - Results are idempotent: the same lease plus the same payload returns `duplicate`. A different payload for
    a finished order gets 409.

## Verification evidence (what makes `tests_verified` true)
A PASS must carry `changes.diff`, `qa-run.json`, `qa-test.log` and `baseline-test.log`.

`qa-run.json` is `{"record": {...}, "signature": {"alg": "HMAC-SHA256", "key_id", "value"}}`, written by the
Workbench server's `verify.py`. The signature is an HMAC over the canonical JSON of the record (sorted keys,
`,`/`:` separators, ASCII). Campus checks all of the following (`workbench_evidence.test_evidence_problems`):
1. The signature verifies with Campus's `AYVEN_ATTESTATION_KEY`, and `key_id` matches. Without a key, nothing
   verifies.
2. `schema == "ayven.verification/v1"`, `verdict == "PASS"`, `reasons == []` and `tamper_findings == []`.
3. `order_key == request_key(order_id, revision)`, so a record cannot be replayed onto another order or onto an
   earlier revision.
4. `allowed_files` equals the order's allow-list.
5. Every artifact matches `record.artifacts[name]` (sha256 and size), and `patch_sha256` is the sha256 of
   `changes.diff`.
6. The `verified` run reported through the host-owned runner, exited 0, and every collected test passed (a skip
   counts only if the same test was skipped at base). At least one test ran, and no test collected at the base
   revision is missing.
7. The log digests match `qa-test.log` and `baseline-test.log`.
8. `changed_files` equals the files named in `changes.diff`, and all of them are inside the allow-list. Campus
   also re-checks the patch scope itself (`workbench_scope.verify_patch`).

**How Workbench produces the record (`verify.py`):**
- It takes the exact patch from trusted git: tracked changes against the recorded base revision plus creation
  patches for untracked files, ignored ones included.
- It builds clean `git archive` trees of BASE and of BASE plus that patch (`git apply`), never using the
  agent's workspace.
- It runs the project's tests in each tree inside bubblewrap through `verify_runner.py`, which is
  bind-mounted read-only. Per-test outcomes come back on a pipe authenticated with a per-run nonce; stdout is
  only a log. pytest or unittest is chosen from the BASE tree.
- An early exit or interference by the code under test means no report, which makes the run BLOCKED. A hang is
  killed at `AYVEN_VERIFY_TIMEOUT` (300 s by default) and the run is BLOCKED. A patch over 100 KB, or a log over
  200 KB, is BLOCKED, never cut.

**Residual risk.** Code under test runs in the same process as the runner. With deliberate introspection it
could forge the runner's report; `tamper_findings` flags the known ways to do this, and the owner previews the
exact patch before deciding. The attestation key is a symmetric secret held by the Workbench host and Campus,
so a fully compromised Workbench host can still sign. An asymmetric key would remove the need for Campus to
hold a signing secret.

## Workbench panel endpoints the worker uses (machine token, loopback only)
| Endpoint | Purpose |
|---|---|
| `GET /api/health` | sandbox posture; the worker claims only when `sandbox == bubblewrap` |
| `POST /api/job` `{mode:"director", brief, project, allowed_files, idempotency_key, token_cap?, allow_clarification?}` | creates or returns the goal derived from the request key; changed facts get 400 |
| `GET /api/state`, `GET /api/board?goal=` | status, stage, task metrics (tokens) |
| `GET /api/result?id=` | `QA-run.json`, `Verified:<artifact>` (exact bytes), `Live-patch-sha256` (the workspace now), `Changes.diff`, `New-files.txt` |
| `POST /api/campus-stop` `{id, request_key}` | stops only a Campus goal, and only with its own request key |

## Local run (disposable)
See the "Run it yourself" section of `workbench/docs/agent-handoffs/execution-backbone-20261023.md` in
Leehiggins111/Milo.
