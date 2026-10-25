# Agent worker tasks (v1, 25 Oct 2026) — registered agents become real workers

A row in the `agents` table is a **registered** agent, not a worker. These actions assign an
authorised task to a specific registered agent and run it on a **real, permitted execution backend**
(the platform's model gateway — local Ollama when configured). Nothing simulates completion: if the
backend is unavailable or returns nothing, the task **fails visibly** and no fixture is substituted.

- Campus implementation: `apps/api/app/agent_worker.py`; exposed through the Ayven OS registry
  (`apps/api/app/ayven_os.py`), so the GUI and Milo invoke the identical, audited actions.
- Branch: `feature/ayven-agent-workers-20261025` (additive; not merged into any integration release).

## Actions (in the shared `GET /os/actions` manifest, category `workforce`)

| id | mutates | input | returns |
|---|---|---|---|
| `agent.assign` | **yes** | `{agent_id, kind, objective, start?}` | `{task}` — runs now unless `start:false` |
| `task.get` | no | `{id}` | `{task}` (includes `evidence`) |
| `task.list` | no | `{agent_id?, status?, limit?}` | `{tasks:[...]}` |
| `task.cancel` | **yes** | `{id}` | `{task}` (a *queued* task → `cancelled`) |
| `task.retry` | **yes** | `{id}` | `{task}` (a *failed* task → a new linked attempt) |

`agent.get` now also returns `active` (true when the agent has a queued/running task — i.e. it is a
working worker, not merely registered) and `permitted_task_kinds` (what it is actually allowed to do).

### v1 task kind
- `research_brief` — the agent produces a short factual brief from `objective` using the model
  gateway. Capability required: `READ_WEB`. It is read/reasoning only: no external effect, no host
  access, and the gated capabilities (`WRITE_FILES`, `SEND_EMAIL`, `PURCHASE`, `RUN_CODE`, …) are
  never used and remain approval-gated in `intelligence/permissions.py`.

### Validation and authorisation (deterministic, server-side)
- The agent must exist (`404`), be `idle` (`409` if working/retired/already tasked), the kind must be
  known (`400`), and the objective must be a non-empty string ≤ 2000 chars (`400`).
- The agent's **role** must hold the capability the kind needs (`permissions.capabilities_for`); if
  not, `403` — and the refusal is audited as `forbidden`. Research roles have `READ_WEB`; agents like
  `compliance`/`outreach`/travel roles do not, so they cannot be given a `research_brief`.

### Task shape
```json
{"id":"tk-…","agent_id":"research-e1","owner":"lee@…","kind":"research_brief","objective":"…",
 "status":"completed","attempt":1,"parent_task_id":null,
 "created_at":"…","started_at":"…","finished_at":"…",
 "execution_mode":"REAL","model":"qwen2.5-coder:3b","tokens":94,
 "result_text":"…","result_sha256":"<64 hex>",
 "evidence":{"execution_mode":"REAL","model":"qwen2.5-coder:3b","tokens":94,"finish_reason":"stop",
             "elapsed_s":…,"tool":"model_gateway","capability":"READ_WEB"},
 "error":null}
```
- `status` ∈ `queued | running | completed | failed | cancelled`.
- `execution_mode`: **REAL** only when a real local model served it (the served model is named);
  **SIMULATED** when the stub produced it (no real model configured) — never mistake one for the other.
- `agent.assign` returns HTTP `200` with a `task` even when the **work** failed (`status:"failed"`,
  `error` set): the *action* ran; the *task* result is what failed. Read `result.task.status`, not
  just the envelope's `ok`. (A failed task has `result_text` empty and `execution_mode` null.)

### Lifecycle
- `start:false` creates a `queued` task without running it; `task.cancel` then moves it to
  `cancelled`. A `running` or already-terminal task cannot be cancelled (`409`).
- `task.retry` on a `failed` task creates a new `queued` attempt (`parent_task_id` set, `attempt`
  incremented) and runs it.

## Proven live
See `docs/evidence/agent-workers/agent_live_evidence.json` (+ `agent_live_harness.py`): a real Campus
(uvicorn + on-disk SQLite) wired to local Ollama. `research-e1` ran a `research_brief` — `execution_mode
REAL`, model `qwen2.5-coder:3b`, 94 tokens, a genuine 565-char answer — the task and its evidence
**survived a full Campus process restart**, the assignment was audited to the owner, and an
unauthorised assignment to `compliance` was refused (`403`, audited `forbidden`).

## Model/tool configuration
- Real: `AYVEN_LOCAL_LLM_BASE_URL=http://<host>:11434/v1`, `AYVEN_LLM_STUB=0`,
  `AYVEN_EMPLOYEE_MODEL=<ollama model>` (e.g. `qwen2.5-coder:3b`). Paid escalation stays off unless
  `AYVEN_ALLOW_ESCALATION=1` with an API key.
- Without a local base (or `AYVEN_LLM_STUB=1`): tasks run but are labelled `SIMULATED`.

## Not in v1 (say so, don't fake)
- Only `research_brief`. Other kinds (web_search/browse, code tasks, outreach) need their tools and
  gated-capability approval wired before they can be offered — a future kind in the same registry.
- Execution is synchronous within the request (bounded first slice); a background worker pool,
  streaming progress and mid-run cancellation of a running task are future work. Approvals/apply
  remain disabled and are not touched here.
