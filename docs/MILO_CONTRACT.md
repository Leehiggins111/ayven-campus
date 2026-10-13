# Campus API for Milo

Campus does not call Milo. Milo calls Campus. In staging, `AYVEN_ENGINE_TOKEN` must equal `AYVEN_ACCESS_KEY`. Leave `AYVEN_MILO_WRITE_SECRET` unset. `MILO_ENGINE_ENABLED=1` and `AYVEN_ENGINE_BASE_URL` point Milo at this API.

Costs are stored in USD. Responses that Milo shows use GBP via `AYVEN_FX_GBP_PER_USD` (default `0.7571`). The effective job cap is the lower of the requested cap and `AYVEN_JOB_BUDGET_USD` (default `$2`). The monthly cap defaults to `$100` and uses the Europe/London calendar month.

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
  "allow_publish": false
}
```

Send `Idempotency-Key`. The same key returns the same project id.

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
