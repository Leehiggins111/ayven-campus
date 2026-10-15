# Campus ↔ Workbench integration contract (specification only)

Status: **proposal, not implemented.** Written 16 Oct 2026 from the code, not from memory.
Owner: Lee Higgins. Implementation belongs to the integration lead; this file changes no runtime code.

Sources read:
- Campus: `Leehiggins111/ayven-campus`, branch `claude/campus-workflow-acceptance` (outcome states, budgets, criteria evidence).
- Workbench: `Leehiggins111/Milo`, branch `cloud/workbench-deploy-20261015` at `1bb69d7`, folder `workbench/` (`server.py`, `team.py`, `reviews.py`, `deploy/`). This is the source deployed on VM `ayven-workbench-cpu` per `workbench/DEPLOYMENT_STATUS_2026-10-15.md`.

## 1. Shape of the integration

Campus stays the **source of truth** for jobs, approvals, budgets, evidence and deliverables. Workbench is a **worker**: it executes one coding work order in an isolated git worktree, verifies it with independent QA, and reports back.

Recommended direction: **Workbench pulls work from Campus over HTTPS** (outbound only from the VM). The VM keeps its current posture: no public port, loopback-only panel, IAP-only SSH. Campus never needs to reach into the VM. This is a recommendation, not a decision; the alternative (Campus pushes into Workbench) needs a private network path that does not exist today (see M1).

```
Milo ──brief──▶ Campus (Cloud Run) ◀──claim / heartbeat / result── Workbench worker (VM, outbound HTTPS)
                    │ approvals, budget, evidence, files                     │ worktree, sandbox, QA
                    ▼                                                         ▼
               Lee approves                                           local Ollama (zero marginal spend)
```

## 2. Identifiers

| Name | Owner | Form | Rule |
| --- | --- | --- | --- |
| `project_id` | Campus | UUID (or UUID5 of Milo's idempotency key) | The job. Never reused |
| `package_id` | Campus | UUID | The top work package the order belongs to |
| `work_order_id` | Campus | `wo_<uuid4>` | One execution request. Stable across Workbench retries of the same order |
| `attempt` | Campus | integer ≥ 1 | Incremented only when Campus re-issues the order (§8) |
| `idempotency_key` | Campus | `<work_order_id>:<attempt>` | Workbench must return the existing run for a repeated key, never start a second one |
| `run_id` | Workbench | its goal id (12 hex) | Reported back; Campus stores it beside `work_order_id` |
| `lease_token` | Campus | opaque | Proves which worker holds the order; every report must carry it |

## 3. Work order (Campus → Workbench)

```json
{
  "work_order_id": "wo_…", "attempt": 1, "idempotency_key": "wo_…:1",
  "project_id": "…", "package_id": "…",
  "task_class": "software_build",
  "objective": "Add multiply(a, b) to calc.py …",
  "acceptance_criteria": [
    {"id": "ac1", "text": "multiply(a, b) returns a*b", "check": "tests"},
    {"id": "ac2", "text": "Existing add() test still passes", "check": "tests"}
  ],
  "constraints": ["Do not publish or install dependencies."],
  "repository": {"url": "https://github.com/…/repo.git", "commit": "<sha>", "path": "."},
  "limits": {"wall_minutes": 30, "task_minutes": 20, "max_model_calls": 200, "max_attempts_per_task": 2},
  "budget": {"amount": 1.25, "currency": "GBP", "source": "sent", "remaining": 1.25},
  "run_mode": "SIMULATED | PARTLY_SIMULATED | UNVERIFIED",
  "lease_token": "…", "lease_seconds": 45,
  "issued_at": "2026-10-16T00:00:00Z"
}
```

Rules:
- `acceptance_criteria` are Campus's criteria, verbatim, each with a stable `id`. Workbench must report a verdict per id (§6).
- `budget` keeps the amount and currency Lee set. Workbench never converts or reduces it; it only reports usage (§6) and stops when told.
- `repository` pins an exact commit. Workbench must refuse an order whose commit it cannot check out, never "use HEAD".
- Anything Workbench cannot honour is refused at claim time with a typed reason (§9), not silently dropped.

## 4. Calls

All JSON over HTTPS to Campus, `Authorization: Bearer <workbench service key>` (a key separate from Lee's access key, at least 32 characters, only in the VM's environment).

| Call | Purpose | Notes |
| --- | --- | --- |
| `POST /workbench/claim` `{worker_id, capabilities}` | Take the oldest runnable order | 204 when none. Returns the work order with a fresh `lease_token` |
| `POST /workbench/orders/{id}/heartbeat` `{lease_token, attempt, stage, progress}` | Renew the lease every ≤ 15 s | 409 `lease_lost` means stop work immediately and discard results |
| `POST /workbench/orders/{id}/events` `{lease_token, events:[…]}` | Board posts, stage changes | Append-only; Campus shows them in the job timeline |
| `POST /workbench/orders/{id}/artifacts` (multipart) | Upload files before the result | Each file: name, sha256, size, kind (`patch`, `source`, `report`, `log`) |
| `POST /workbench/orders/{id}/result` `{lease_token, …}` | Final outcome (§6) | Exactly once per attempt; a repeat with the same body is a no-op, a different body is 409 |
| `GET /workbench/orders/{id}` | Is the order still wanted? | Campus answers `active`, `stopped` or `superseded` |

## 5. Status mapping

Workbench internal states stay internal. Only these reach Campus:

| Workbench (goal `jobs.status` / task) | Reported `worker_state` | Campus workflow / outcome |
| --- | --- | --- |
| claimed, `queued` | `accepted` | `IN_PROGRESS` |
| `running`, any stage | `running` | `IN_PROGRESS` (stage shown) |
| `ready_for_review` (last QA `done`, verdict PASS) | `succeeded` | → Campus review, then `AWAITING_APPROVAL` → `NEEDS_APPROVAL` |
| `blocked` because QA failed after the correction round | `failed_qa` | `FAILED` (never approval) |
| task `failed` (exception, no progress, timeout) | `failed_error` | `FAILED`, typed blocker |
| `blocked` because a task was refused by `validate()` / sandbox missing | `refused` | `BLOCKED` with the reason |
| `stopped` | `stopped` | `BLOCKED` (Lee stopped) or no change if Campus initiated |
| `interrupted` (VM stopped, lease expired twice) | `interrupted` | stays `IN_PROGRESS` until Campus re-issues or fails it (§8) |

## 6. Result (Workbench → Campus)

```json
{
  "lease_token": "…", "attempt": 1, "run_id": "a1b2c3d4e5f6",
  "worker_state": "succeeded | failed_qa | failed_error | refused | stopped",
  "base_commit": "<sha>", "result_commit": "<sha on branch ayven/<run_id>>",
  "qa": {"verdict": "PASS | FAIL", "evidence": ["Ran `python -m unittest`: exit 0, 5 tests …"], "defects": []},
  "criteria": [{"id": "ac1", "status": "met | not_met | unverified", "evidence": "…"}],
  "tests": {"command": "python -m unittest", "exit": 0, "count": 5, "baseline_count": 2},
  "artifacts": [{"name": "changes.patch", "sha256": "…", "size": 1234, "kind": "patch"}],
  "usage": {"model_calls": 14, "prompt_tokens": 22100, "model_seconds": 410, "wall_seconds": 980,
            "model": "qwen3:4b-instruct", "backend": "ollama-local", "marginal_cost": {"amount": 0, "currency": "USD"}},
  "failure": null,
  "simulated": false
}
```

Rules:
- `qa` is Workbench's own independent QA (`team.qa`), parsed with the strict parser (`reviews.parse_review`). A PASS with defects or without evidence is not a PASS.
- `criteria` is per Campus criterion id. `unverified` is allowed; inventing `met` is not.
- Campus re-checks: it re-evaluates its criteria against the returned artifacts, records both results as evidence, and its own approval gate (unmet criteria need Lee's explicit override) still applies.
- `marginal_cost` is what the order spent beyond the VM. Local Ollama is 0. Campus records it in the ledger with `usage`. VM time is a fixed cost and is reported, not charged per job.
- Workbench applies nothing to any real repository. `/api/apply` stays a manual, local action.

## 7. Artifacts

- Required: `changes.patch` (full `git diff base_commit..result_commit`, not truncated), every new or changed file as `source`, the QA `review.json` as `report`, and stage logs (`log`, secrets redacted) with a size limit.
- Campus stores them in `stored_files` (database, survives restarts) and serves them through `bundle.zip`, so the existing outcome labels (DRAFT / REJECTED / SIMULATED) apply unchanged.
- Hashes are checked on upload. A result whose artifacts are missing or mismatched is rejected (409) and the order stays open.

## 8. Retries and recovery

| Situation | Who acts | Behaviour |
| --- | --- | --- |
| Task fails inside a run | Workbench | Its existing policy: `max_attempts=2` per task, one correction round after a QA FAIL, then report |
| Worker process restarts mid-task | Workbench | Lease recovery (`team.recover`) requeues once; Campus lease keeps the order claimed while heartbeats continue |
| Heartbeats stop for > `lease_seconds` × 3 | Campus | Marks the attempt `interrupted`, frees the order |
| Interrupted order | Campus | Re-issues with `attempt+1` up to its attempt cap, only if the job is still within budget and not stopped; otherwise `FAILED` with blocker `timeout` |
| Lee rejects the result | Campus | Job ends `REJECTED`. **No automatic rework** (matches Campus today) |
| Duplicate claim or result | Both | Idempotency key and lease token make repeats no-ops |

## 9. Failure kinds (typed, never free text only)

`repo_unavailable`, `commit_not_found`, `sandbox_unavailable`, `model_unavailable`, `tests_failed`, `qa_failed`, `no_progress`, `timeout`, `stopped`, `lease_lost`, `budget_exhausted`, `unsupported_order`, `internal_error`. Each carries `message` (plain English) and optional `detail`. Campus maps them onto its blocker kinds and its FAILED report.

## 10. Mismatches between the code as it is today and this contract

| # | Today in Workbench (`1bb69d7`) | Today in Campus | Consequence | Owner |
| --- | --- | --- | --- | --- |
| M1 | Panel binds `127.0.0.1:8765` and refuses any `Host` other than loopback; the VM has no public port | Campus runs on Cloud Run | **Campus cannot reach Workbench at all.** Needs the pull model (§1) or a private path | Integration lead |
| M2 | Auth is a random token generated at each process start and scraped from the panel HTML (`deploy/test-real-job.py`) | Bearer access key | No stable service credential; every restart breaks a caller | Workbench |
| M3 | `POST /api/job` creates a fresh id; no external id, no idempotency | `project_id`, `package_id`, idempotency keys | A retried submission starts a second run (the smoke script works around it with a local file) | Workbench |
| M4 | Brief is free text; `plan()` writes fixed acceptance strings per class (`build`/`review`/`research`) | `acceptance_criteria` list, per-criterion evidence | Campus criteria are lost or only embedded in prose; no per-criterion verdict comes back | Both |
| M5 | `project` is a folder path that must already exist on the VM, clean, as a git repo | Jobs have no repository field | Campus has nothing to send; repos must be provisioned on the VM by hand | Both |
| M6 | Limits are time and turns (`minutes`, `turns`, `stage_budget` is legacy-only); no money budget or ledger | Job budget with currency, monthly cap, spend ledger | Budget cannot be enforced or reconciled; usage metrics exist (`calls`, `prompt_tokens`, `model_seconds`) but are not exported | Both |
| M7 | Goal `blocked` means **QA failed or work not done** | `BLOCKED` means **waiting for Lee** | Same word, opposite meaning. Must be mapped (§5), never passed through | Integration lead |
| M8 | Status only via `/api/state` (last 80 jobs) and `/api/board?goal=` | Per-job status | No per-run endpoint; older runs drop out of the list | Workbench |
| M9 | `/api/result` returns logs and `Changes.diff` truncated to 80 KB, and only the **names** of new files | Files stored in the database, served as `bundle.zip` | New files' contents and large diffs cannot be retrieved; no hashes | Workbench |
| M10 | Retries: per-task attempts, one correction round, manual `/api/retry` | Lease attempts and durable checkpoints per job | Two retry systems with no shared attempt number | Both |
| M11 | Failures are free-text `result` strings | Typed blockers and failure report | Campus cannot classify Workbench failures | Workbench |
| M12 | VM stops itself after 2 h (`--max-run-duration=2h`) | Expects workers to be available | Work in flight is interrupted; laptop-off operation needs a longer or on-demand lifetime, which costs money and needs Lee's approval | Lee |
| M13 | `ready_for_review` → owner runs `/api/apply`, which fast-forward merges into the VM's project folder | Approval completes a package and serves files | "Apply" is a local merge on the VM, not a delivery; Campus approval must not trigger it | Integration lead |
| M14 | No dispatch hook: Campus's employee stage calls its own model runtime (`AYVEN_AGENT_RUNTIME`) | — | A Workbench executor adapter is needed at that stage | Integration lead |
| M15 | Workbench reports `simulated` nowhere; a fake model in tests and the real model are indistinguishable in results | Every result carries `run_mode` / `simulated` | Must be reported per run | Workbench |

Resolved since the 14 Oct handover: the contradictory-verdict check. The cloud source routes the native team through `reviews.parse_review`, which fails a PASS that lists defects or has no evidence. `tests/test_qa_gate.py` passed here on 16 Oct (4 tests), alongside the path and timing tests (12 of 13; the 13th needs bubblewrap, which this workspace lacks).

## 11. Not in scope / not decided

- Running the VM continuously, GPU, or any paid model: **not authorised**; zero marginal spend assumes local Ollama.
- Who provisions repositories on the VM (M5) and how the sandbox gets them without network access.
- Whether Campus or Workbench owns a "rework" loop after Lee rejects.
