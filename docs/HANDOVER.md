# Handover

One place to resume. No deploy and no spend were done in this run. Measured spend: £0. Do not close or merge Campus PRs #2 or #5.

## Branches, commits, PRs

| Repo | Branch | PR | State |
|---|---|---|---|
| Leehiggins111/ayven-campus | `cursor/ayven-workforce-fe56` | Draft #6 against `cursor/ayven-v2-workable-a9eb` | Open. Recovery, Milo contract, remote libsql HTTP, hosted boot refusal |
| Leehiggins111/ayven-campus | earlier recovery PRs | #2 and #5 | Open. Superseded by #6. Not closed, not merged |
| Milo | acceptance repairs | #23 at `3956f43` | The Campus client this handover cites. Not re-tested in this run |

This round is `72979fe`. It builds on `215efb5` (durable mode is the only job runner) and `67c2cff` (Milo constraints and recovery).

## What is true now

Durable mode is the only job runner. `AYVEN_THREAD_STUB=1` is a local test switch and does not execute a job. Outside `AYVEN_ENV=local` it refuses to start.

Application data uses SQLite, a `file:` libsql URL, or remote libsql over HTTP (Hrana `/v2/pipeline`). `libsql://` is opened as HTTPS. `?tls=0` stays HTTP for a local sqld. Embedded replica sync is not used. DBOS system tables stay on local SQLite beside the process. They are not moved to Turso.

Hosted mode is `RENDER` set, or `AYVEN_REQUIRE_DURABLE=1`. If `AYVEN_DB_URL` or `AYVEN_DB_AUTH_TOKEN` is missing, startup raises and does not open temporary SQLite. A `file:` URL is refused in hosted mode. `/health` reports `durable` true or false and `persistence_state` `durable` or `not_durable`. Local SQLite is `not_durable`. Remote HTTP with a token is `durable`.

A dead `dbos:<pid>` lease can be claimed on the next start, so a new process can resume when the workflow database was wiped. Finished stages are skipped from `durable_checkpoints`. `record_review` goes through the outbox (`side_effects`). An employee-stage crash no longer doubles it.

A delivery review that exhausts `AYVEN_MAX_ATTEMPTS` writes a blocker and a pending approval for Lee, same as planning. A rejected job result is the stage, the cause, and the next action. It is not `Engine stopped: ValueError`.

Qwen-Agent is used when the package imports and `AYVEN_AGENT_RUNTIME` is `auto`. If that tool loop raises, the employee turn returns the native prose and records the error in `fallback`. The Ollama path uses `/api/generate` for a Qwen model and `/api/chat` otherwise (`uses_qwen_chatml`).

## Tests

`apps/api/tests` except `test_campus_browser.py`: **350 passed, 12 failed**. The 12 are optional runtimes that are not installed here (Playwright, crawl4ai, browser-use, qwen-agent, MCP stdio, sandbox preflight). The browser test was not run.

Proved locally: hosted boot refusal; `/health` durable flag; sqld HTTP row surviving a second process; employee crash keeping `record_review` at 1; review cap writing a blocker; qwen tool-loop failure returning native text.

## Cost

From `fullscope/cost-final.md`. Do not treat the estimate as measured spend.

| | Figure |
|---|---|
| Measured spend, this run | £0. No pod, no Turso database created, no paid API |
| Expected setup | £63/month estimate (range £48 at 50 GPU-hours to £79 at 150) |
| Live test cap | $1.10 Community / $2.75 Secure, maximum 3 hours |
| Job cap | `AYVEN_JOB_BUDGET_USD` default 2 |
| Month cap | `AYVEN_MONTHLY_BUDGET_USD` default 100 |

`bash scripts/run_ayven_live.sh` without `AYVEN_CONFIRM_SPEND=YES` prints the plan and exits 0.

## Remaining defects

- This branch is not on Render. The live service is still ephemeral SQLite until Lee sets the two database variables and deploys.
- DBOS workflow rows do not move to Turso. A redeploy drops them. Resume depends on the remote checkpoints, the outbox, and reclaiming a dead lease.
- `validation/wf1.py` is SIMULATED. It does not prove model quality.
- Stub results can still be titled "Final result". WhatsApp is not told when a job needs Lee. The Milo desktop needs Lee's computer.
- Customer products, including the salon AI Receptionist, are not connected. Lee has not confirmed which copy is the real one.
- The 12 optional-runtime failures above, and the browser test, were not turned into passes.
- Backup models (Qwen3-30B-A3B, then 8B BF16, then 32B) stay UNVERIFIED and off.

## Environment

| Name | Role |
|---|---|
| `AYVEN_DB_URL` | Remote `libsql://` or `https://` database. Required in hosted mode |
| `AYVEN_DB_AUTH_TOKEN` | Token for that database. Required in hosted mode. Never commit it |
| `AYVEN_REQUIRE_DURABLE` | `1` forces hosted rules outside Render |
| `RENDER` | Set by Render. Turns hosted rules on |
| `AYVEN_ACCESS_KEY` | At least 32 characters on a non-local deploy. `AYVEN_ENGINE_TOKEN` must equal it |
| `CAMPUS_BASE_URL` | Where Milo calls Campus |
| `AYVEN_MILO_WRITE_SECRET` | Leave unset |
| `MILO_ENGINE_ENABLED` | `1` on Milo |
| `AYVEN_ENV` | `local` for the crash hook and the thread stub |
| `AYVEN_CONFIRM_SPEND` | `YES` only for a GPU run Lee approved |
| `AYVEN_JOB_BUDGET_USD`, `AYVEN_MONTHLY_BUDGET_USD` | Spend caps |

## Hosted acceptance on Render

Do this after the two database variables are set and this branch is the running build. No GPU. Stub model is enough. `/health` must show `durable: true`, `persistence: libsql_http`, `persistence_state: durable`.

1. **Jobs and results survive a restart and a redeploy.** Submit one job. Record the project id, status, result, and `record_review` count. Restart the Render service. Read the job back. Redeploy the same commit. Read it again. The id, result, and review count match. Removing either database variable must stop the process from booting, with an error that names the missing variable. It must not serve SQLite. Put the variables back before leaving it down.
2. **Interrupted work resumes.** Submit a second job. Restart while it is `running`. After `/health` is durable again, that job is not left `running`. It is complete, waiting, or rejected. The rejected text names the stage, the cause, and the next action. There is one project, not two.
3. **The review runs once.** Compare `record_review` rows and `side_effects` rows (`action=record_review`) with a control job that was not restarted. They match. A further restart adds none.

Local stand-ins, not substitutes for the three steps above: `test_remote_hrana_rows_survive_a_new_process_and_health_is_durable` (sqld), `test_a_barrier_kills_the_process_mid_stage_and_the_restart_finishes`, `test_hosted_mode_refuses_to_start_without_a_remote_database`.

## Lee's actions

1. Point `AYVEN_DB_URL` and `AYVEN_DB_AUTH_TOKEN` at an existing Turso database before deploying this branch. Deploying without them makes Render fail to boot.
2. Deploy #6. Set `AYVEN_ACCESS_KEY` (32 characters or more) and set `AYVEN_ENGINE_TOKEN` to the same value. Leave `AYVEN_MILO_WRITE_SECRET` unset. Point Milo's `CAMPUS_BASE_URL` at that service.
3. Run the hosted acceptance above.
4. Read `docs/LIVE_TEST.md` before any GPU run. The only spend switch is `AYVEN_CONFIRM_SPEND=YES`, inside the $1.10 / $2.75 cap.
5. Leave PRs #2 and #5 open until #6 is the one you merge.

```bash
python3 -m pytest apps/api/tests/test_hosting.py apps/api/tests/test_durable.py apps/api/tests/test_leases.py apps/api/tests/test_delivery_plan.py -q
bash scripts/run_ayven_live.sh
```

## Milo end-to-end defect fixes and pre-pilot fixes (since `4a2d946`)

### QA-verified at `3975f80` (CI green, 369 passed; the browser tests failing locally were a box environment issue, not code)

| Commit | Change |
| --- | --- |
| `6f0296d` | Delivery review and repair model calls run before the MANAGER stage write (`Programme.delivery_calls`), removing the `database is locked` failure. `run_retries` records the exact error as a blocker and creates no approval. A failed deliverable is checked before the escalation branch. `fail_project` records the exact cause as a blocker |
| `da58a43` | Tests for the above in `test_qa_defects.py` |
| `9a780c8` | `finalize_project` saves generated files (`blobs.store_package_files`), so `bundle.zip` serves them |
| `0f849cd` | `goals.evaluate_criteria` fills `criteria_results`; `/milo/jobs/{id}` returns them |
| `1a6309d` | `job_cap_gbp` stored exactly in GBP; a budget above `AYVEN_JOB_BUDGET_USD` is refused with HTTP 400, never clamped |
| `72c0861` | `/health` runtime comes from `durable.enabled()`; `durable` needs a durable runtime and durable storage |
| `bf562c0` | Earlier version of this handover section |
| `733264b` | Stale review-cap test updated: an unparseable review is a failed step, so a blocker and no approval |
| `b2d6caa` | Criteria match whole tokens; figures must appear exactly (`£25` is not `£250`) |
| `3975f80` | e2e (`validation/wf1.py`) and `MILO_CONTRACT.md` example budgets £2.00 to £1.00, under the unchanged $2 ceiling |

### Pending QA

| Commit | Change |
| --- | --- |
| `7c3e998` | `/health` runs a live `SELECT 1` (`db.live_check`, 2s timeout) when storage is configured durable. On failure it reports `durable: false`, `persistence_state: degraded`, `persistence_live: false` and `persistence_error` |
| `711836f` | Criteria keep figures of any length (`3`, `£5`, `$2`) and match them exactly as whole tokens, after normalising `£25.00` to `£25` and `1,000` to `1000` |

Run: `python3 -m pytest apps/api/tests/test_hosting.py apps/api/tests/test_qa_defects.py -q`, then the full suite.

## Backlog: criteria matching

Criteria checks are deterministic whole-token coverage, not a model judgement. Known gaps:

- **Negation.** "No setup fee" is met by a report that says "a setup fee applies".
- **Hyphen versus space.** "30-day" and "30 day" are different tokens.
- **Plurals.** "prospect" does not match "prospects".
