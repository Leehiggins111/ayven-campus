# Campus first-release lane: prototype orders, review, Lee's fix decision, teams (for Codex)

Branch `feature/campus-prototype-review-20261109`, base `feature/ayven-agent-workers-20261025` @ f679f5f
+ merge of `security/release-hardening-20261024` @ 91cb5c0 (clean merge, 34e1cbf). Brief: Milo
`docs/delivery/FIRST-RELEASE.md` @ 74b1193. Codex owns the Ayven lifecycle and final integration; nothing here
merges or deploys anything.

## What Campus now does

| Route | Who | What |
|---|---|---|
| `POST /ayven/prototypes` | owner | Queue a build of an **approved** `ayven.plan/v1` revision. Refused (409) unless the approval names this plan's sha256, id and revision. Idempotent per `<job_id>:plan-rev-<n>`. |
| worker `claim/heartbeat/result` | Workbench | Existing leases, fences, expiry and cancellation reused (`workbench_orders.kind = 'prototype'`). Claim sends the plan **without** `plan.review` (held-out checks). |
| `GET /ayven/prototypes[/{id}]` | owner/read | State, build checks, findings `{passed, failed}`, honest mode label, private prototype link, `decision_required`. |
| `GET /ayven/prototypes/{id}/prototype/{index.html,style.css,app.js}` | owner | Only after review; CSP blocks all network. |
| `POST /ayven/prototypes/{id}/review` | owner | Re-run a review that did not complete (idempotent). |
| `POST /ayven/prototypes/{id}/decision` | owner | After a failed build or review: `fix` queues **one** fix order (`…:fix-<n>`, findings sent to Workbench) or `no_fix` closes. One decision per order. |
| `GET /ayven/teams`, `POST /ayven/teams/assign`, `POST /ayven/teams/{team}/release` | owner | Teams = existing departments + agents. One job per team per role. |
| `GET /ayven/priority`, `POST /ayven/priority/{id}/decide` | owner | Competing jobs → Campus recommendation with reasons → waits for Lee. |

Review = verify Workbench signature over `ayven.prototype/v1` → file bytes match signed hashes → built from the
approved plan digest → mode from signed provenance (REAL only for verified Ollama; FIXTURE/SIMULATED labelled) →
Campus's own `web_browser_runner.py` re-runs the approved journeys **plus** `plan.review.checks` at 1280/390px →
JS-disabled copy must fail. Any failure → `review_failed`, nothing rebuilt until Lee decides.

## Interfaces Codex needs to confirm (Ayven lifecycle)

1. After Lee approves a plan, the lifecycle calls `POST /ayven/prototypes {plan, approval}` (shape in
   `apps/api/app/prototype_review.py:ApprovalIn`). Planning-team assignment is released at that moment.
2. The lifecycle should `POST /ayven/teams/assign {job_id, role:"planning", team:"command", inputs}` when Ayven
   starts planning, so competing plans produce a priority decision.
3. `plan.review.checks` (optional) are held out from Workbench; who authors them (Ayven planner vs Campus
   reviewer) is a lifecycle decision.

## Evidence (all local; nothing hosted)

`tests/test_prototype_review.py` 17 tests: approval binding, idempotency, lease-retry identity, integrity
(tampered bytes), FIXTURE never REAL, privacy of files, failed review waits for Lee, one fix build with findings,
decline, read-only refusal, teams recommendation + Lee overrule, review waiting on a busy team. Two tests run the
REAL Campus browser runner (headless Chromium): Alba fixture passes 8/8 checks; a deliberately broken Alba (caps
six-panel doors at 2) passes Workbench's £260 journey but fails the held-out £255 check → `review_failed`.
Full suite: 210 passed, 1 failed — `test_line_endings` (CRLF artifact of the Windows worktree, also fails on base).
