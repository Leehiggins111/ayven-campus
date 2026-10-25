# Ayven OS action layer (v1, 25 Oct 2026) — contract for the UI and Milo

Ayven OS is the platform; Milo is one assistant inside it. Every operation here is reachable the same way from the
**graphical interface** and from **Milo**, with the same permissions and the same audit record.

- Campus implementation: `apps/api/app/ayven_os.py` (branch `feature/ayven-os-service-layer-20261025`).
- Milo proxy: `src/server/os/os-routes.js` (same branch in `Leehiggins111/Milo`), mounted at `/api/os`.
- **Not merged into the integration release.** It is additive; it changes no existing endpoint.

## How to call it
There are two equivalent front doors; the payloads are identical.

| Caller | Base | Auth |
|---|---|---|
| Graphical interface / any owner client | Campus `…/os/…` | `Authorization: Bearer <AYVEN_API_TOKEN>` or the Campus session cookie |
| Milo (assistant and browser UI) | Milo `/api/os/…` | the owner's Milo session; Milo forwards under its Campus key and adds `X-Ayven-Owner` |

Routes (Campus path shown; Milo is the same under `/api/os`):

| Method & path | Purpose |
|---|---|
| `GET /os/actions` | The action manifest — the contract this document describes, machine-readable. |
| `GET /os/actions/{id}` | One action's contract. |
| `POST /os/actions/{id}` | Invoke an action. Body is the action's input object. Owner only; mutating actions reject a read-only token. |
| `GET /os/audit?limit=&action=` | The durable audit trail of every invocation. |

A read-only token (`AYVEN_READ_TOKEN`) may `GET` the manifest and the audit trail but cannot invoke anything
(invocation is `POST`, which the Campus boundary refuses to a read-only token).

## The manifest (`GET /os/actions`)
```json
{"schema": "ayven.os/v1",
 "actions": [
   {"id": "agent.create", "title": "Create an agent", "category": "agents", "mutates": true,
    "auth": "owner", "method": "POST", "path": "/os/actions/agent.create",
    "input_schema": {"name": "string (required)", "role": "string (required)",
                     "department_id": "string (required)"},
    "example_input": {"name": "Grace Researcher", "role": "Research analyst", "department_id": "research"}},
   ...]}
```
Render buttons/forms straight from `input_schema` and `example_input`; never hard-code the action list.

## Actions in v1
| id | category | mutates | input | returns |
|---|---|---|---|---|
| `agent.list` | agents | no | `{department_id?}` | `{agents: [{id,name,role,department_id,status,progress,tokens,cost_usd,created_at,created_by,managed_by}]}` |
| `agent.get` | agents | no | `{id}` | `{agent}` |
| `agent.create` | agents | **yes** | `{name, role, department_id}` | `{agent}` (new `id` `ag-…`, status `idle`, `managed_by:"ayven-os"`) |
| `agent.retire` | agents | **yes** | `{id}` | `{agent}` (status `retired`) |
| `department.list` | agents | no | `{}` | `{departments:[{id,name}]}` |
| `project.list` | projects | no | `{limit?}` | `{projects:[{id,title,objective,status,created_at}]}` |
| `project.get` | projects | no | `{id}` | `{project, tasks:[...]}` |
| `execution.list` | executions | no | `{limit?}` | `{executions:[{id,project_id,objective,state,attempts,created_at}]}` |

Invocation response shape:
```json
{"action": "agent.create", "ok": true, "audit_id": "<hex>", "result": {"agent": {"...": "..."}}}
```

### Example — create an agent (through Milo)
```
POST /api/os/actions/agent.create
Content-Type: application/json
{"name": "Grace Researcher", "role": "Research analyst", "department_id": "research"}

200 {"action":"agent.create","ok":true,"audit_id":"9f...","result":{"agent":{
     "id":"ag-1a2b3c4d5e6f","name":"Grace Researcher","role":"Research analyst",
     "department_id":"research","status":"idle","managed_by":"ayven-os","created_by":"lee@…"}}}
```
The new agent is a row in the platform's real `agents` table, so it also appears in `GET /state` and the 3D view.

### Errors (same JSON shape everywhere)
`{"detail": "..."}` from Campus (Milo passes `{ "error": "...", "code": "..." }`). Statuses:
- `400` bad or missing input (e.g. empty `name`, unknown `department_id`, a name with control/bidi characters);
- `401` not signed in (or a read-only token on `POST`); `403` a mutating action with a read-only token that reached
  the handler; `404` unknown action or unknown row; `409` a guard (e.g. retiring a busy agent);
- Milo turns a Campus `401` of **its** key into `502` (your sign-in is unaffected); Campus unreachable is `503`.

## Audit (`GET /os/audit`)
Every invocation — allowed or refused, ok or failed — is one durable row **before** the response is returned:
```json
{"entries": [{"id","ts","actor","action","mutates","input_sha256","input_summary","outcome","detail"}]}
```
`outcome` ∈ `ok | refused | forbidden | bad_request | not_found`. `actor` is the signed-in owner (the
`X-Ayven-Owner` Milo forwards) or the principal kind. Only a short `input_summary` and a `input_sha256` are kept,
never raw secret-bearing payloads. Filter with `?action=agent.create`.

## Through Milo's chat (Stage 3)

The owner can also invoke these actions by **talking to Milo**. Milo understands the request, picks
the matching action, fills its parameters, and runs it through the exact routes above — it never
mutates state any other way, and it never decides who the owner is. The safety envelope lives in
`src/server/os/assistant.js` (Milo):

- **Identity** is the authenticated Milo session (Google browser session or device bearer), read
  server-side and forwarded as `X-Ayven-Owner`. Owner-bearing fields in the model's tool input are
  stripped; a chat message can never set the owner.
- **Mutations are confirmed.** A create/retire is not sent on the first request; Milo asks the owner
  to confirm and sends it only after an explicit, deterministically-read "yes". "no" abandons it;
  the pending item expires after 5 minutes.
- **Reads** run immediately (still through the owner boundary and audit).
- **Failures are honest.** A Campus `400/401/404/409`/unreachable is reported as a failure; nothing
  is claimed to have changed.
- **A question is not an operation.** If the request is not an operation, Milo answers in chat and
  calls no action.

`POST /chat` (and the browser chat route) return the normal reply plus, when an OS action was
involved, an `os` block and a `refresh` hint for the UI:

```json
{"reply": "Done — create an agent \"Grace Researcher\" (Research analyst) in research. (ag-1a2b…)",
 "route": "os", "backend": "ayven-os",
 "os": {"ok": true, "status": "ok", "action": "agent.create", "audit_id": "9f…",
        "result": {"agent": {"id": "ag-1a2b…", "status": "idle", "managed_by": "ayven-os"}},
        "refresh": ["agents"]}}
```

`os.status` ∈ `ok | confirm_required | cancelled | bad_request | not_found | conflict | unauthorized
| unavailable | model_error | model_unavailable`. On `confirm_required`, `os.needs_confirmation` is
true and `os.summary` describes what will happen; the next message decides it.

Example conversations:

```
Owner: create an agent called Grace Researcher, a research analyst, in research
Milo:  This will create an agent "Grace Researcher" (Research analyst) in research. Shall I go ahead? (yes/no)
Owner: yes
Milo:  Done — create an agent "Grace Researcher" (Research analyst) in research. (ag-1a2b3c4d5e6f)   [refresh: agents]

Owner: list the agents in research
Milo:  You have 2 agents:
       • Grace Researcher (Research analyst) — idle
       • GUI Agent (Ops lead) — idle

Owner: retire agent ag-1a2b3c4d5e6f
Milo:  This will retire agent ag-1a2b3c4d5e6f. Shall I go ahead? (yes/no)
Owner: no
Milo:  Okay — I won't retire agent ag-1a2b3c4d5e6f. Nothing was changed.

Owner: create an agent in the marketing department        (no such department)
Milo:  (after confirmation) I couldn't do that: Unknown department_id 'marketing'; create it first or use an existing one.
```

## Guarantees and limits
- **Persistence** is the real Campus SQLite database; there is no mock.
- **Authorisation** is the existing Campus owner boundary; nothing here lowers it.
- **It exposes existing capabilities only.** It does not enable owner approvals, unsandboxed execution, deployment,
  or anything the platform has switched off. v1 mutating actions touch the agent registry only — never code
  execution, the Workbench lane's approval/apply, or production configuration.
- **Not yet implemented (say so in the UI, don't fake it):** creating/retiring departments; editing an agent's
  role after creation; project creation through the OS layer (use the existing `POST /projects`, which spins the
  workforce); automations, files, integrations and settings beyond budget. These are future actions to add to the
  same registry.
