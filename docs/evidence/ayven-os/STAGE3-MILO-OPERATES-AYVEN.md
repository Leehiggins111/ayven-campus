# Stage 3 — Milo operates Ayven: live evidence (25 Oct 2026)

Milo turns a natural-language request into an **authorised OS action**, runs it through the same
`/os/actions` registry the graphical interface uses, and reports the real result. This note records
the live proof against a **real running Campus** (uvicorn + on-disk SQLite), i.e. not a mock.

- Campus branch: `feature/ayven-os-service-layer-20261025`.
- Milo branch (same name): assistant layer `src/server/os/assistant.js`, wired into `ai/milo.sendMessage`.
- Harness: [`os_live_harness.py`](os_live_harness.py) + [`os_live_server.py`](os_live_server.py);
  raw result: [`stage3-live-evidence.json`](stage3-live-evidence.json).

## How the proof maps to the Definition of Done

| Requirement | Evidence (live) |
|---|---|
| Milo uses the authorised OS action, not a side door | `milo_create` → `POST /os/actions/agent.create` with `Bearer <owner>` + `X-Ayven-Owner: lee@ayven.local` — byte-identical to what Milo's proxy sends — returns `ok`, `managed_by: "ayven-os"`. |
| The agent appears in Ayven's graphical Agent Workforce | `state_before_restart` → both the GUI-created and Milo-created ids are present in `GET /state` (the 3D workforce view reads this). |
| The operation survives restart | `state_after_restart` → after the uvicorn process is killed and a **fresh process** starts on the same SQLite file, both agents are still in `/state`, and the audit trail still has all 7 rows. |
| It appears in the audit trail, with the real owner | `audit` → the Milo create's actor is `lee@ayven.local` (the forwarded session owner); the GUI create's actor is `owner:bearer`. |
| GUI and Milo create consistent persisted state and audit records | Same table, same `/state`, same audit shape; the only difference is the recorded actor. |
| Natural-language understanding selects the action | Proven deterministically in `src/server/os/assistant.test.js` (scripted model → tool selection + slot fill); the live harness drives the resulting authorised request. |
| Rejected requests fail closed | `rejected_unknown_department` → `400`, nothing created. |
| Unauthorised requests fail closed | `readonly_cannot_invoke` and `unauthenticated_refused` → `401` at the access boundary (before the handler), so a read-only token cannot mutate. |
| Guarded (destructive) requests are guarded | `retire_busy_blocked` → `409` (a working agent); `retire_missing_404` → `404`; `retire_idle_ok` → `200` retired. In Milo's chat, a retire/create is additionally held for an explicit owner **confirmation** before it is ever sent (see `assistant.js`). |
| Honest audit | Refusals are recorded with the **documented** outcome — `bad_request`, `not_found`, `forbidden` — not a catch-all; a 409 guard stays `refused` with the status in the detail. Outcomes seen: `['bad_request','not_found','ok','refused']`. |

## Reproduce

```bash
export CAMPUS_ROOT=/path/to/campus
export AYVEN_DB=/tmp/os-live.db AYVEN_ENV=local AYVEN_LLM_STUB=1 OS_PORT=8801
export AYVEN_API_TOKEN=$(python -c "print('o'*40)") AYVEN_READ_TOKEN=$(python -c "print('r'*40)")
python docs/evidence/ayven-os/os_live_harness.py   # writes stage3-live-evidence.json
```

## Not covered here / risks

- The NL→action step in the live harness uses a scripted tool-capable model; a run against the real
  Anthropic model (requires an API key, costs money) is the next confirmation. Local Ollama has no
  tool-calling, so Milo declines operations honestly when only Ollama is configured.
- Pending confirmations in Milo are in-process (lost on a Milo restart) and keyed per channel.
- Stage 4 (project management, recurring automations) is intentionally not started until this slice
  is signed off.
