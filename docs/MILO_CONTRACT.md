# Campus API for Milo

Campus does not call Milo. Milo calls Campus. In staging, `AYVEN_ENGINE_TOKEN` must equal `AYVEN_ACCESS_KEY`. Leave `AYVEN_MILO_WRITE_SECRET` unset. `MILO_ENGINE_ENABLED=1` and `CAMPUS_BASE_URL` point Milo at this API.

Costs are stored in USD. Responses that Milo shows use GBP via `AYVEN_FX_GBP_PER_USD` (default `0.7571`). A `job_cap_gbp` is stored and returned exactly as sent, in GBP. `AYVEN_JOB_BUDGET_USD` (default `$2`) is the per-job ceiling and the cap when none is sent. A budget above the ceiling, or not above zero, is refused with HTTP 400 and a reason; it is never clamped. The monthly cap defaults to `$100` and uses the Europe/London calendar month.

## Create a job

`POST /projects` and `POST /milo/jobs`.

```json
{
  "objective": "Prepare the listing drafts for the Scottish Short-Term Let Host Kit. Publish nothing.",
  "acceptance_criteria": ["Drafts stay unpublished", "Prices match the commercial file"],
  "deadline": "2026-10-20",
  "job_cap_gbp": 2.0,
  "budget_usd": null,
  "allow_paid_fallback": false,
  "allow_publish": false,
  "constraints": ["Publish nothing", "Do not invent a price"]
}
```

Send `Idempotency-Key`. The same key returns the same project id. A different title with that key is the same job, not a 409. A different objective with that key is a 409. A new key is a new job, including when the objective matches a job that is still running.

`constraints` is stored on the job and given to the director and to QA as hard constraints. `GET /milo/jobs/{id}` includes `mode` (`REAL`, `SIMULATED`, `DEMO`, `DRY_RUN`, `PARTLY_SIMULATED`, or `UNVERIFIED`) and `constraints`.

`allow_publish` records intent. A publish attempt creates an approval and does not call a platform.

## Budget

`GET /budget` returns `monthly_cap_gbp`, `month_spent_gbp`, `month_remaining_gbp`, and `resets_at`.

`PUT /budget` with `{ "monthly_cap_gbp": 100 }` changes the monthly cap.

A job that would pass the cap pauses with a blocker of kind `cap`. Paid fallback is a separate blocker and stays off unless the job and the process both allow it.

## View

`GET /campus/view` includes `blockers` (`kind` is `cap`, `fallback`, `access`, `error`, or `deadline`), `files` (`name`, `size`, `kind` for bytes actually stored), and `criteria_results` (`criterion`, `status`).

`GET /work-packages/{id}/bundle.zip` returns those stored files. It does not invent a `main.py` link.

## Decisions

`POST /approvals/{id}/resolve` with `{ "decision": "rejected", "reason": "..." }`. A rejection without `reason` is HTTP 400: `Add a reason so Ayven knows what to change.`

`POST /work-packages/{id}/accept`

`POST /work-packages/{id}/rework` with `{ "reason": "..." }`

`POST /work-packages/{id}/stop`

`PATCH /work-packages/{id}` with `{ "job_cap_gbp": 2.0 }`

## Restart

Set `AYVEN_RESUME=1`. On startup Campus re-dispatches projects still marked `running` whose package is not waiting for Lee. Finished employee calls are not repeated.

## Checklist

| Row | Status | Evidence |
|---|---|---|
| R1 | Implemented and verified | Job and monthly caps. `test_a_job_over_its_cap_halts`, `test_the_monthly_cap_refuses_a_new_job`. `GET /budget` and `PUT /budget`. |
| R2 | Implemented awaiting live verification | Scripted harness tasks. Not a model run. |
| R3 | Implemented awaiting live verification | Tool calls are gated. A live per-step log is not in this branch. |
| R4 | Implemented awaiting live verification | Files and traces are rows. `test_files_and_traces_survive_a_process_restart`. Turso needs both `AYVEN_DB_URL` and `AYVEN_DB_AUTH_TOKEN`. |
| R5 | Implemented awaiting live verification | Scripted handovers. Not a model run. |
| R6 | Implemented awaiting live verification | Scripted reject-and-fix. `POST /approvals/{id}/resolve` requires a reason. Not a model run. |
| R7 | Implemented awaiting live verification | Lease, fence, and outbox tests. Not a live host restart. |
| R8 | Implemented awaiting live verification | Scripted completion gate. Not a model run. |
| R9 | Implemented and verified | `test_r9_reasoning.py`. |
| R10 | Implemented awaiting live verification | `scripts/run_ayven_live.sh` is plan-only until `AYVEN_CONFIRM_SPEND=YES` on a pod. |
| R11 | Implemented awaiting live verification | This branch is not deployed. |
| R12 | Implemented awaiting live verification | `Idempotency-Key` and `test_a_repeated_submission_returns_the_same_project`. The Node harness was not run. |
| R13 | Implemented awaiting live verification | `GET /campus/view` returns blockers, files, and criteria. No browser pass. |
| R14 | Implemented awaiting live verification | Simulated `run_metrics`. No GPU cost report. |
| R15 | Implemented awaiting live verification | Bearer or a session cookie that expires after 12 hours. `test_a_session_older_than_twelve_hours_is_refused`. |
| R16 | Implemented awaiting live verification | Simulated harness passes. GPU run not done. |
| R17 | Implemented and verified | `allow_paid_fallback` defaults off. `test_escalation_disabled_makes_no_external_call`. |
