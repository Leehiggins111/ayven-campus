# Connected Ayven journey — integration contract v1

Owner: Milo/Codex integration lead. Date: 15 October 2026.
Status: implementation in progress; not a hosted acceptance claim.
Canonical coordination branch in both repositories: integration/connected-journey-20261015.

## Work ownership

| Agent | Owns | Avoid without coordination |
|---|---|---|
| Claude Code | Milo browser UI, owner Google sign-in, browser sessions | src/server/campus integration and workbench/ |
| Grok | workbench native worker, QA, sandbox, recovery | bridge protocol/agent, shared deploy scripts |
| Claude | Campus UI, approvals, acceptance tests | workbench_bridge.py, internal worker routes |
| Milo/Codex | integration contracts, worker bridge, Milo Campus adapter, deploy/rollback | other agents' UI/auth implementations |

Keep separate branches. Record source base, commit, changed paths, tests and blockers in docs/agent-handoffs/. A health check or installed model is not a usable product.

## Existing bases inspected

- Campus cd207e24b139d85d278748f4c3971d8421b7e403, cursor/ayven-workforce-fe56.
- Milo cloud/milo-verified-20261015 @ 0f19481237a45e8ac4fd0f53baf5eaa1c6eed072.
- Workbench cloud/workbench-deploy-20261015 @ 1bb69d7532465f5a6f8b6adbd824a4581b3b2aba.
- Workbench branch contains the Milo base plus workbench/; use it as the Milo integration base.

## Protocol decisions

Campus is the durable control plane. The VM pulls jobs over HTTPS; neither Ollama nor Workbench needs an inbound public port. No Cloud Shell tunnel is part of the execution path.

Explicit execution requests POST /milo/executions with Idempotency-Key and an allowlisted project alias (initially cloud-demo). Do not turn arbitrary research briefs into code jobs. Project aliases map to local Git roots in VM configuration; callers cannot provide shell commands or filesystem roots.

Campus assigns project_id and package_id once. Workbench receives that stable project_id as its submission key. A canonical request fingerprint includes the objective, project alias, constraints and acceptance criteria. Reusing a key with a changed payload returns 409. A transport timeout is uncertain delivery, not permission to create a new job.

Worker protocol: POST /internal/workbench/claim, /heartbeat and /result. Claims have a lease and monotonically increasing fence. A stale worker cannot overwrite a reclaimed or terminal job. Lost responses are safe to retry. A result acknowledgement is persisted before it is returned.

Browser identity is separate from Milo-to-Campus service authentication and the dedicated worker bearer credential. Browser cookies and ordinary Campus read tokens never authorize internal worker routes. Worker secrets are mounted outside agent workspaces and never returned by APIs.

## Lifecycle

queued -> running -> awaiting_approval -> completed
queued -> awaiting_clarification -> queued
running -> blocked | failed
queued/running/awaiting_clarification/awaiting_approval -> stopped
awaiting_approval -> rejected

Only an authenticated owner acceptance marks completed; awaiting_approval means QA passed and bytes were durably saved, not that work was accepted or applied. Rejection requires a reason. No bridge operation applies changes, publishes, pays or pushes code. A worker outage leaves a pending/recoverable job rather than fabricated completion.

A worker result includes protocol_version, project_id, worker_job_id, fence, status, execution_mode, QA verdict/evidence/defects, test output, and bounded file content with SHA-256. PASS with defects, missing evidence, unavailable tests, no deliverable bytes or simulated work claimed REAL is rejected. Artifacts are stored through Campus; the browser downloads via its authenticated Milo/Campus session, never a worker URL.

Existing clarification/status/approval/download operations in Milo remain supported for existing Campus jobs. Bridge jobs additionally expose execution details; integration supplies their adapter. UI agents must surface mode, lifecycle and evidence without inferring success from HTTP 200.

## Verification and release

Track separately: protocol tests using controlled providers; actual model capability; hosted end-to-end acceptance. Never count the first as either of the latter.

Acceptance: owner signs in, submits a code brief, answers any clarification, observes worker progress, inspects QA and files, rejects or accepts, refreshes without losing state. Replay submissions, lost responses, restart and stale-result rejection must not duplicate work or produce false completion.

No live traffic promotion until a pinned candidate has passed health and acceptance checks. Preserve previous revisions for rollback. Do not extend the two-hour VM runtime, upgrade hardware or add paid services silently.

## Current gaps

Milo browser sign-in is assigned to Claude Code. Workbench real cloud build/QA and restart acceptance are assigned to Grok. Campus workflow acceptance is assigned to Claude. The bridge is being implemented by Milo/Codex. Hosted acceptance is pending all four components.
