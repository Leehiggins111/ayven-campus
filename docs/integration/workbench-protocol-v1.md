# Campus ↔ Workbench protocol v1 — implementation branch

Status: **EXPERIMENTAL / NOT RELEASE-READY**. This is the canonical endpoint namespace for the integration branch. The alternative `docs/integration/campus-workbench-contract.md` remains useful as a requirements inventory but its `/workbench/orders/*` routes are **not implemented**. No production deployment is authorized.

## Boundary and execution

Campus is the control plane. Workbench **pulls** over HTTPS; Workbench has no public inbound port. Owner routes use the existing Campus bearer/session boundary. Worker routes require a **separate** `AYVEN_WORKER_TOKEN` of at least 32 characters. Existing Campus API tokens and browser cookies must not authenticate worker routes.

The new `POST /milo/executions` route is **opt-in** and does not trigger the legacy Campus orchestrator. Existing `POST /milo/jobs` remains unchanged. `GET /milo/jobs/{project_id}` adds an optional `execution` projection.

| Route | Principal | Purpose |
|---|---|---|
| POST /milo/executions | owner | Enqueue bounded job, requires Idempotency-Key |
| GET /milo/executions/{order_id} | owner | State and evidence |
| POST /milo/executions/{order_id}/decision | owner | Explicit accept/reject |
| GET /milo/executions/{order_id}/artifacts/{name} | owner | Only after owner acceptance |
| POST /internal/workbench/claim | worker | Atomic queued/expired claim, fence increment |
| POST /internal/workbench/{order_id}/heartbeat | worker | Extend current lease |
| POST /internal/workbench/{order_id}/result | worker | Fenced verdict and bounded UTF-8 artifacts |

## Contract

Enqueue JSON: `{"objective":"...","project_alias":"cloud-demo","acceptance_criteria":["..."],"constraints":[],"job_cap_gbp":0}`. Only `cloud-demo` is allowed. Key is required, max 200 characters; payload is fingerprinted and changed replays are rejected.

Claim request: `{"worker_id":"worker-1"}`. Response is `{"job":null}` or a job with `order_id`, `project_id`, `objective`, `project_alias`, `acceptance_criteria`, `constraints`, `budget_gbp`, `fence`, `lease_seconds`. Claim is transactional. Lease is 90 seconds; after expiry the job can be reclaimed up to three attempts. Stale workers cannot heartbeat or submit.

Result request: `{"worker_id":"worker-1","fence":1,"verdict":"PASS","execution_mode":"REAL","evidence":["test output"],"defects":[],"artifacts":[{"name":"hello.py","content":"print('hello')\n","sha256":"..."}]}`. Up to 10 UTF-8 artifacts, max 256 KiB each, SHA256 checked. PASS requires nonempty evidence and no defects; QA PASS becomes `awaiting_approval`, **never automatically completed**. Owner acceptance is separate. A simulated PASS cannot be accepted as a real deliverable.

## Known limitations / must-fix before any staging

- **Not tested end-to-end**; no CI evidence, no local Qwen execution.
- Workbench adapter currently **fails closed** at the structured QA export boundary: it can claim and submit a Director goal, but cannot yet extract full production QA evidence and artifact bytes. It reports BLOCKED rather than inventing PASS.
- The v1 evidence payload is worker-supplied and not cryptographically attested to a particular production QA invocation. The independent auditor must verify this before trust.
- Owner approval uses a new execution-specific decision endpoint, not the existing work-package approval path. Consolidate before staging.
- Recovery depends on the configured Campus database being genuinely persistent. Hosted Cloud Run durability remains unverified.
- Cancellation, durable outbox for retrying results, and duplicate-result replay acknowledgement are not implemented.
- The worker's 10-second heartbeat loop can lose its lease on network interruption; it stops on error, but manual recovery must be tested.
- Workbench's unsafe QA guard must be repaired on its own branch before any real PASS is trusted.
- Local-only HTTP is permitted by adapter only with `AYVEN_ALLOW_HTTP_LOCAL=1`; no new paid resources or secrets were created.

## Run preparation (not an execution claim)

Set `AYVEN_WORKER_TOKEN` independently on Campus and worker; configure `AYVEN_CAMPUS_URL`, `AYVEN_PANEL_TOKEN`, and `AYVEN_CLOUD_DEMO_PROJECT` on the worker. Start the existing Workbench server with its normal sandbox protections; then run `python3 workbench/src/campus_worker.py`. Do **not** start unattended until the integration tests and QA fix pass.
