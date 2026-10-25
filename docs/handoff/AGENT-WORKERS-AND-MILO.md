# Handoff — executable Ayven workforce + Milo operating it (25 Oct 2026)

From the backend/intelligence lead. For Codex (integration/security) and Claude (frontend).

## 0. Baseline discrepancy — read first
The brief named `integration/ayven-os-unified-20261025` with Milo `46e7326…` and Campus `ab44e1b…`.
**Neither that branch nor either commit exists on `Leehiggins111/Milo` or `Leehiggins111/ayven-campus`**
(verified with `git ls-remote`). I did not build on a phantom base or overwrite anything. I built
additively on the newest *real* OS/agent foundation — my pushed `feature/ayven-os-service-layer-20261025`
(Campus `0fbdc9f`, Milo `b154521`), which already carries the OS action registry and Milo assistant.
**Codex: please confirm the true integration baseline.** If the unified branch exists locally/elsewhere,
rebasing these additive changes onto it should be clean (new files + a registry append + one audit-mapping line).

## 1. Branches and commits (dedicated, additive, NOT merged)
| Repo | Branch | Commit | Contents |
|---|---|---|---|
| ayven-campus | `feature/ayven-agent-workers-20261025` | `81425bb` | worker lane (`agent_worker.py`), OS actions, tests, live evidence, audit-outcome fidelity |
| Milo | `feature/ayven-agent-workers-20261025` | `2311426` | assistant uses the worker actions, honest task-status reporting, tests, e2e evidence |

Both branch off the OS service-layer branch. No protected-branch merge, no deploy, approvals/apply untouched and still disabled.

## 2. What now works (primary task: registered agents → real workers)
A registered agent (a row in `agents`) is now assignable a real task that runs on a real backend.
- **Backend:** the platform model gateway (`llm`/`models`), i.e. local Ollama when configured. No host
  access, no gated capability. `execution_mode` is **REAL** only when a real model served it (named),
  **SIMULATED** for the stub — never confused.
- **Lifecycle:** `queued → running → completed | failed`; `queued → cancelled`; `failed → retry`.
  Durable `agent_tasks` rows; each carries linked evidence (served model, tokens, finish_reason,
  sha256 of output).
- **Validation + authorisation** reuse the real permission model (`intelligence/permissions.ROLE_CAPS`):
  agent must exist, be idle, kind known, objective valid, and the **role must hold the capability**
  (research_brief needs READ_WEB). Research roles qualify; compliance/outreach/travel do not (403, audited).
- **Honest failure:** if the backend is down/empty the task is `failed` with an error and no substituted
  result; `agent.assign` still returns HTTP 200 with that failed task — read `result.task.status`.

**v1 is one kind, `research_brief`** (a short factual brief), deliberately proven before expanding.

## 3. API contract changes (coordinate before integrating)
New Ayven OS actions (shared manifest `GET /os/actions`, category `workforce`; full spec in
`docs/contracts/agent-workers-v1.md`):
- `agent.assign` (mutating) `{agent_id, kind, objective, start?}` → `{task}`
- `task.get` `{id}` → `{task}` (incl. `evidence`); `task.list` `{agent_id?, status?, limit?}` → `{tasks}`
- `task.cancel` (mutating) `{id}`; `task.retry` (mutating) `{id}`
- `agent.get` now also returns `active` (has a queued/running task) and `permitted_task_kinds`.
Also: the OS **audit** now records the documented outcome (`bad_request|not_found|forbidden`) instead
of a catch-all `refused` (already on the OS branch; unchanged behaviour otherwise).

Milo: no new HTTP surface — it proxies `/api/os/*` as before. The `/chat` reply's `os` block gains
`task_status` when a task is produced. Action ids are dotted (no underscores) to pass Milo's proxy id check.

## 4. End-to-end execution evidence (real, reproducible)
- Campus-direct (`docs/evidence/agent-workers/agent_live_evidence.json`, harness alongside): real Campus
  (uvicorn + on-disk SQLite + Ollama). `research-e1` ran a research_brief → **REAL, qwen2.5-coder:3b,
  94 tokens, genuine 565-char answer**; task + evidence **survived a full process restart**; audited to
  the owner; unauthorised `compliance` refused (403, audited forbidden).
- Through Milo's own client (`Milo docs/evidence/ayven-os/milo-worker-e2e.json`): `createCampusClient`
  `.osInvoke('agent.assign',…,'lee@milo.local')` → `research-e2` task **completed, REAL, qwen2.5-coder:3b,
  489 chars**, read back with evidence, audit actor `lee@milo.local`. Same task visible to Ayven (`/state`,
  `/os/audit`) and Milo — acceptance test 5.

## 5. Model/tool configuration used
`AYVEN_LOCAL_LLM_BASE_URL=http://127.0.0.1:11434/v1`, `AYVEN_LLM_STUB=0`,
`AYVEN_EMPLOYEE_MODEL=qwen2.5-coder:3b` (Ollama; `qwen3:4b-instruct` also present). Campus runs in WSL
distro `ayven-audit-test` via `/opt/campus-venv`. Paid escalation stays off.

## 6. Tests and security checks
- Campus: `tests/test_agent_worker.py` 10, `tests/test_ayven_os.py` 10; full `tests/` **132 passed**
  (only pre-existing `test_line_endings` fails — a CRLF artifact of this Windows worktree).
- Milo: `src/server/os` assistant/workers/integration/os-routes **29 passed** (in `npm test`).
- Security preserved: owner auth + boundary unchanged; per-role capability enforcement; no host access;
  Workbench sandbox/signed-evidence/approvals untouched and disabled; mutations require explicit owner
  confirmation in Milo; identity is server-session only (never model/body). Acceptance tests 1–8 met
  (8 = existing suites pass, modulo the pre-existing CRLF test).

## 7. Milo intelligence (secondary task)
Milo distinguishes **registered** agents (`agent.list` labels them; `agent.get.active`) from active
workers (a queued/running task). It can answer "what workers do I have" (agent.list), "what can they do"
(`permitted_task_kinds`), "which jobs need attention" (task.list status=failed), and "assign this task
to a suitable worker" (agent.assign, confirmed, deterministic auth — not model text). Consequential
actions are confirmed and run through authenticated backend ops. Honest reporting: a failed/SIMULATED
task is never called done.

## 8. MEMORY audit (no change made — not destabilising the execution work)
- **Persisted (Milo SQLite):** `memories` (durable personal facts: category/key/value/timestamp) and
  `conversations` (channel/direction/sender/message/timestamp — history, not memory). Two distinct tables.
- **CRUD verified** on memories: create (upsert on category+key), get, listAll/recent/grouped, update,
  remove (delete), search — facts can be saved, retrieved, corrected and deleted.
- **Scope:** one owner per Milo instance (the authenticated owner's DB). No multi-tenant per-user column.
- **Consent/sensitive data:** saving is implicit (the owner tells Milo); stored in plaintext SQLite;
  `storage-redact.js` redacts logs. No explicit consent prompt or classification.
- **Automatic memory:** the `save_memory` tool + persistence are real and unit-tested, but *automatic*
  triggering depends on the chat model deciding to call it and is **not independently verified** — do not
  claim automatic memory is reliable yet.
- **Safe improvement proposed (deferred):** owner-driven, audited memory actions (`memory.save/list/forget`)
  so memory writes are deterministic/authenticated rather than only model-triggered; and an explicit
  consent/sensitivity flag. Left as a next slice to avoid destabilising this release.

## 9. Instructions for Codex integration
1. Confirm the real integration baseline (see §0); rebase these two branches onto it (additive).
2. Campus: `agent_worker.py` is new; `ayven_os.py` gains handlers + 6 `_register` lines + the audit-outcome
   map + `agent.get` enrichment. `main.py` already mounts the OS router. Run `tests/test_agent_worker.py`.
3. Milo: only `src/server/os/assistant.js` changed (+ new test); no chat/route change this round.
4. Keep approvals/apply disabled. To enable REAL execution in an environment, set the three env vars in §5;
   otherwise tasks run SIMULATED and are labelled as such.

## 10. Frontend requirements for Claude
- Render a worker's state from `agent.get` (`status`, `active`, `permitted_task_kinds`) — show "registered"
  vs "working".
- An **assign** form from the `agent.assign` manifest entry (agent picker, kind, objective); it is mutating,
  so surface the confirmation Milo already enforces.
- A task view from `task.get`/`task.list`: show `status`, and for `execution_mode:"SIMULATED"` show a clear
  "placeholder result" badge; for `failed` show the error and a **Retry** affordance (`task.retry`); a
  **Cancel** on a `queued` task (`task.cancel`). Refresh `agents`+`tasks` on the `os.refresh` hint.
- Never present a `failed`/`queued`/`SIMULATED` task as completed.

## 11. Remaining limitations / risks
- One task kind; synchronous execution (bounded); no background worker pool, no streaming progress, no
  mid-run cancel of a *running* task. Future kinds (web/browse, code, outreach) need tools + gated-capability
  approval first.
- REAL execution needs a configured local model; without it, SIMULATED (labelled).
- Memory improvements deferred (§8). Baseline reconciliation pending (§0).
