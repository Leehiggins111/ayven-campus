# Campus workflow and acceptance: handoff

Owner: Lee Higgins. Author: Claude (Campus workflow/UI, approval behaviour, independent acceptance).
No secret values are in this file.

## Where the work is

| Item | Value |
| --- | --- |
| Repository | Leehiggins111/ayven-campus |
| Branch | `claude/campus-workflow-acceptance` |
| Based on | `cursor/ayven-workforce-fe56` at `cd207e24b139d85d278748f4c3971d8421b7e403` |
| Commits | see `git log cd207e2..claude/campus-workflow-acceptance` (CI_COMMITS) |
| CI | CI_RESULT |
| Deployed? | **No.** Nothing here was deployed. `ayven-workforce` still runs whatever revision `ayven-workforce-00001-zw4` contains. |

## Evidence classes used below

- **CI (simulated):** GitHub Actions on this branch, `AYVEN_LLM_STUB`/deterministic role generator, temporary SQLite. Proves logic, not model quality or hosting.
- **Hosted (read-only):** requests to `https://ayven-workforce-lsdckqlo5a-nw.a.run.app` on 15 Oct 2026. No jobs were created there.
- **Code reading:** I read the code; nothing was run.

## What I changed and why

| # | Gap found (code reading) | Change | Proven by |
| --- | --- | --- | --- |
| 1 | No single answer to "how did this job end". A job Lee rejected and a job whose step failed were both just `FAILED`; simulated results looked like real ones | New `app/outcome.py`: `COMPLETED`, `NEEDS_APPROVAL`, `NEEDS_CLARIFICATION`, `IN_PROGRESS`, `BLOCKED`, `REJECTED`, `FAILED`, plus `simulated` and `evidence` (`SIMULATED` / `LIVE_UNVERIFIED`). Added as `outcome` to `/milo/jobs/{id}` and `/campus/view`; shown in the Campus UI as a badge | `test_campus_workflow_outcomes.py`; harness |
| 2 | `deliverable.md` served any package's text unlabelled, including rejected drafts. `files/{name}` served source for any state. Milo's `result` showed a rejected draft as the result | `deliverable.md` starts with a banner unless the result is accepted and live (DRAFT / REJECTED / FAILED / BLOCKED / SIMULATED), names itself `-diagnostic`/`-DRAFT`/`-SIMULATED`, and sends `X-Ayven-Outcome` / `X-Ayven-Evidence`. `files/{name}` returns 409 unless COMPLETED or a draft under review. `bundle.zip` for a Lee rejection starts `# REJECTED. NOT A DELIVERABLE.`; for blocked/unfinished jobs it adds `NOT-ACCEPTED.txt`. Milo's `result` for rejected/failed/blocked work starts with `REJECTED. NOT A DELIVERABLE.` (the text is still there, labelled) | tests + harness |
| 3 | Approving a draft landed it COMPLETED even with an unmet acceptance criterion (the existing program test did exactly this: "Adds 4 and 7" is `not_met`) | `POST /approvals/{id}/resolve` refuses (409, with each unmet criterion and its evidence) unless `accept_unmet_criteria: true` **and** a reason. The override is stored (`approvals.accepted_unmet`) and the outcome says `UNMET CRITERIA ACCEPTED BY LEE`. A COMPLETED package with unmet criteria and no override is `BLOCKED`, never an accepted deliverable. `POST /work-packages/{id}/accept` takes the same optional body | tests + harness |
| 4 | Acceptance checks stored only `met`/`not_met`, with nothing to show what was checked | Each result now stores evidence: method, terms, found, missing, coverage, report SHA-256 and length, timestamp (`criteria_results.evidence_json`). New `criteria_evidence` on status and view. `criteria_results` keeps its old shape | tests + harness |
| 5 | A USD budget was displayed as pounds and labelled GBP. The monthly cap was stored only in dollars, so the pounds Lee set were re-derived. A budget the month could not afford created a job row before being refused, leaving an orphan "running" job | Budgets keep amount and currency as sent (`job_cap_amount`, `job_cap_currency`, `job_cap_gbp_is_conversion`). The monthly cap keeps its pounds exactly (`monthly_cap_currency`). The month check runs before the job exists; a replay of an existing job is still returned | tests + harness |
| 6 | `POST /work-packages/{id}/rework` replied "Ayven will rework it", but nothing ever acts on a rework blocker | The reply now says rework does not start automatically and returns `rework_started: false`. **Re-running work is worker dispatch, so I did not build it** (see blockers) | test |
| 7 | UI: "Ask Milo" sent only the objective; every action swallowed errors; download links for `deliverable.md`, `main.py` and `test_main.py` appeared for rejected and failed work | Brief form with acceptance checks and a £/$ budget (sent as entered; refusals shown), an unsent brief survives a refresh (browser storage), outcome and SIMULATED badges, downloads gated by outcome, criteria list on the approval card, explicit "accept although N checks are not met" tick with a reason, and visible errors on approve/reject/clarify | page test; browser test still drives approve/reject/clarify |
| 8 | `/health` says `durable: true` while the job engine's own (DBOS) tables live on the instance's local disk | Added `workflow_store`, `workflow_store_durable`, `durability_caveats`. I did **not** flip `durable`, because deployment gates may read it; the integration lead should decide | test; hosted read |

Existing tests changed on purpose: two tests in `test_qa_campus_faults.py` approved a job whose own criterion was `not_met` and expected COMPLETED. They now prove the plain approval is refused and then approve with an explicit, reasoned override.

## Acceptance harness

`validation/acceptance_journey.py`, run in CI by `tests/test_acceptance_journey.py`; CI uploads the JSON report as the `acceptance-report` artifact.

SIMULATED mode runs Campus in child processes on a temporary database with a deterministic role generator:

1. Brief with a criterion and a £1.25 budget → idempotent replay → draft labelled `NEEDS_APPROVAL (SIMULATED)`, budget kept → criteria evidence → plain approval refused → reasoned override → COMPLETED, labelled → downloads.
2. Draft → rejection needs a reason → REJECTED → only a labelled diagnostic; files 409; accept 409.
3. Failing build → FAILED, no approval → nothing acceptable; criteria `not_met` with evidence.
4. Clarification → empty answer refused → the same package continues.
5. Budgets: £500 refused with nothing created; a $1.50 budget stays USD; monthly £41.17 kept.
6. **Restart:** a fresh process on the same database re-reads every job; outcomes, criteria evidence and download hashes must be identical.
7. **New instance:** the local workflow store is deleted first (what a new Cloud Run instance sees) and the same re-check runs.

HOSTED mode (`--hosted URL`) is separate evidence: read-only by default (`/health`, unauthenticated access refused, optional `--job ID`); `--allow-writes` adds only a budget probe that is refused before anything is created. It needs `AYVEN_ACCEPTANCE_KEY`.

Results: HARNESS_RESULT

## Hosted evidence (read-only, 15 Oct 2026)

- `GET /health`: `version 2.0.2`, `label SIMULATED`, `runtime durable`, `persistence libsql_http`, `durable true`, `persistence_live true`, `access_protected true`, `research_mode fixtures`, `local_endpoint false`.
- `GET /state` without credentials returned no job data (the fetch was refused; the exact status code was not visible to my tool).
- `version` is `2.0.2` on every PR #6 commit, so `/health` cannot say which commit is deployed. The hosted build predates this branch (no `workflow_store` or `outcome` fields).
- **Not established:** any hosted job, restart, approval, rejection, download or budget behaviour. Full hosted end-to-end acceptance remains NOT established.

## Persistence: what is durable

| State | Where it lives | Survives refresh | Survives restart / new instance |
| --- | --- | --- | --- |
| Jobs, packages, approvals, blockers, criteria + evidence, budgets, ledger, stored files | App database (Turso when hosted) | Yes | Yes by design; proven in CI on SQLite (harness steps 6–7); **not proven hosted** |
| Job engine (DBOS) step records | Instance-local SQLite next to the app DB | n/a | **No.** New instances re-enqueue unfinished jobs from app-database checkpoints and leases (`durable.initialize`). Proven for finished jobs in CI step 7; an in-flight job across a new instance is untested |
| Live event stream (SSE subscribers) | Process memory | Reconnects | Lost; the UI re-polls every 2.5 s |
| Unsent brief text | Browser storage | Yes (new) | Per browser only |
| Typed approval reason / clarification answer before sending | Page memory | **No** | No |
| Local backup (`/admin/backup`) | Instance disk | n/a | No; says so in its reply |

## Defects and remaining blockers

1. **Rework never runs (open, not mine to fix).** A rework request is recorded as a blocker only. Starting the work again belongs to worker dispatch; integration lead to decide.
2. **The criteria checker is crude (open).** It needs ≥70% of a criterion's words to appear literally, so a correct program fails "Adds 4 and 7" because "adds" never appears. The new override makes this explicit rather than silent, but a better checker (or the Workbench reviewer) is needed.
3. **`/health` `durable` is optimistic (open, for the integration lead).** Reported alongside the new `workflow_store` fields; not changed.
4. **Hosted build is identifiable only by revision name.** Recommend `/health` reports the git commit (deployment file change → integration lead).
5. **Campus→Workbench execution is not connected.** Not touched; no bridge work duplicated.
6. **Milo needs small updates (Milo repo, not changed here):**
   - `src/server/campus/acceptance.e2e.test.js` expects a rejected draft from `deliverable.md` unlabelled and `findings.includes(shown.slice(0, 40))`; the banner now comes first. It also expects `bundle.zip` 404 after rejection, but Campus has returned a `report.md` diagnostic since `77adbdf`.
   - Milo's approve call should pass `accept_unmet_criteria` and a reason when Lee knowingly accepts unmet checks, and show the 409 detail otherwise.
   - Milo can show `outcome.label` and `criteria_evidence` directly.
7. **Pre-existing:** a second package can stay "under review" on rejected/completed jobs (from the earlier handover); not reproduced or changed here.
8. **I could not run tests in my own workspace** (package registries are blocked there). All test results come from GitHub Actions on this branch.

## For the next person

- Merge order suggestion: review this branch against PR #6 (`cursor/ayven-workforce-fe56`), then rebase Milo's acceptance test.
- To check a hosted deployment of this branch later: `AYVEN_ACCEPTANCE_KEY=… python validation/acceptance_journey.py --hosted https://ayven-workforce-lsdckqlo5a-nw.a.run.app --report hosted.json` (read-only).
