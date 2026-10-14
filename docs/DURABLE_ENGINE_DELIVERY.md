# Ayven/Milo engine delivery candidate

This change keeps the existing intelligence engine and Campus UI. It adds a
DBOS 3.2.0 worker, transactional application checkpoints, private access and a
read adapter for the current Milo client. It is an integration candidate, not
proof that the full Ayven/Milo product has launched.

## Behaviour

- `AYVEN_DURABLE=1` queues projects in a single-worker durable queue. Startup
  recovers jobs and reconciles project admissions interrupted before enqueue.
- Preparation and each role's database mutations commit in the same SQLite
  transaction as a JSON checkpoint. A replay reads that receipt even when the
  process died before DBOS recorded the step result. IDs do not change.
- Project completion updates result/status and the stable engine task.
- Approval resolution and package completion commit together. Duplicate
  decisions replay the result; contradictory decisions return HTTP 409.
- Clarification continuation uses the existing same-package implementation,
  wrapped in a durable atomic step; it updates project status afterwards.
- POST `/projects` accepts `Idempotency-Key`; reusing a key with a changed
  objective/title returns HTTP 409.
- `AYVEN_API_TOKEN` grants private browser/API access. Browser sign-in is at
  `/login`; sessions are HTTP-only, expire in 12 hours and become invalid on
  key rotation. `AYVEN_READ_TOKEN` is a distinct GET-only credential for Milo.
- `/api/v1/products`, `/api/v1/projects`, `/api/v1/missions` and their detail
  routes match Milo's current read client. Project and mission IDs are the same
  engine project ID. This does not import or replace legacy Ayven-OS products.
- Workforce live mode fails when no endpoint is configured. It does not label
  fixture text as live inference. Hosted JSON mode removes local decoder
  extensions and retains local schema checks.

## Run locally

Copy `.env.engine.example` to `.env.engine`, then run:

```sh
docker compose -f compose.engine.yaml up --build -d
```

Open `http://localhost:8000`. Named volume `ayven-engine-data` contains both the
application database and workflow database. The compose port is bound to the
local machine. Docker build/run was not executed in this environment because
Docker is unavailable; the actual Python runtime/API paths were tested.

The example defaults to fixtures for acceptance checks. For real work, set
`AYVEN_LLM_STUB=0`, `AYVEN_RESEARCH_MODE=live`, the workforce base URL/key and
all three model IDs. `AYVEN_PROVIDER_FORMAT=json_object` selects ordinary JSON
mode plus local validation; `openai` sends strict JSON Schema; `qwen` preserves
local-runtime decoder fields. Provider support and live output quality must be
qualified against the chosen endpoint. No paid calls have been made here.

## Private cloud deployment

Use one process/one replica, a persistent volume mounted at `/data`,
`AYVEN_DB=/data/ayven.sqlite`, `AYVEN_DURABLE=1`, `AYVEN_PUBLIC_MODE=1`, a random
API key of at least 32 characters, and a separate read key. Put the app behind
HTTPS. Do not use the old free Render manifest's ephemeral database as a
production data store. A provider key and a confirmed hosting workspace are
required before live acceptance. No new paid hosting has been provisioned.

For a staging Milo read connection, set server-side `AYVEN_API_BASE_URL` to
this engine's URL and `AYVEN_API_SECRET` to the read token. Keep the current
production Milo/Ayven-OS connection until migration is proven. No secrets
belong in the Electron renderer. New Milo task submission and cloud cutover
remain separate delivery work; this change supplies the read contract only.

## Verification and limits

Process-level tests cover admission crash, crash within employee database
writes, crash after the application commit but before the DBOS receipt,
repeated enqueue, approval replay/conflict and API submission through completed
Milo-compatible readback. Fixture calculation returns 42.00 with one parent and
one persisted call per role. Access tests cover bearer/session/read-only use.
Hosted-provider request shape is mocked; it is not a live-model benchmark.

SQLite stages serialize database writes and may hold a transaction during model
or research calls. This is a bounded single-worker runtime, not a high-throughput
multi-replica service. Repeated external HTTP inference can occur after an
interruption; only database mutations have the atomic receipt guarantee.
External sending/purchasing and filesystem side effects are not covered by this
transaction. Live Qwen session state requires an explicit serializer before
using persistent in-memory sessions. The current worker never auto-authorises
outbound actions.

Back up the application and workflow files together while the worker is stopped.
Do not delete a named volume during upgrades. State envelope version 1 and DBOS
application version `durable-v1` are deliberately stable for this candidate;
future workflow/state shape changes require an explicit compatibility migration.

Keep Campus PR #1 as the UI owner branch. This engine change is a separate PR
against that branch so Grok can review the contract before integrating it.

## Local validation record — 9 October 2026

The broader suite run completed with **111 passed, 1 skipped, 8 deselected**.
The excluded checks were Campus Playwright UI, browser-use local browsing, two
Crawl4AI/browser routes, code sandbox network isolation, browser-dependent
registry/preflight capabilities, and live embedding retrieval. Chromium download
failed in this host and unprivileged user namespaces were denied. The full
suite is retained in GitHub Actions; exclusions were not committed to CI.
Earlier full local runs failed on those environment prerequisites; they are
not represented as passing. Final focused recovery/access/provider/bridge checks
are recorded in the PR alongside this broader run. No GPU or paid provider call
was executed.
