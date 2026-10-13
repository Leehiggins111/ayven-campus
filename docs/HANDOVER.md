# Handover

Branch: `cursor/ayven-workforce-fe56`. Base: `cursor/ayven-v2-workable-a9eb`.

This branch merges the stacked acceptance-repairs work into v2 and adds the workforce controls (goals, budget, files in the database, optional Turso, simulated acceptance run) plus the four QA fixes below. No deploy, no Render change, no pod, no spend.

## QA defects

| Defect | What changed | Test |
|---|---|---|
| R7 restart leaves a run `running` | Startup calls `resume_unfinished` when `AYVEN_RESUME=1`. A killed in-progress package is continued. A package that already has its manager call is closed without a second parent or a second employee call. Packages waiting on Lee stay waiting. | `test_startup_resumes_a_killed_run_without_redoing_finished_work`, `test_resume_keeps_the_same_package_and_does_not_repeat_model_calls`, `test_resume_returns_the_same_package` |
| Tests share `/tmp/ayven-campus.db` | Each test gets its own `AYVEN_DB` under pytest's temp directory. | `test_each_test_has_its_own_database`, `test_a_sibling_test_does_not_see_the_other_database` |
| Retries have no backoff, and a timed-out thread can write later | `run_retries` waits `AYVEN_RETRY_BACKOFF_S * 2^(n-1)`, capped by `AYVEN_RETRY_BACKOFF_CAP_S` (default base 1s, cap 8s; the suite sets the base to 0). After the cap it writes a blocker and a pending approval. `run_bounded` cancels a run token; later inserts from that worker raise and are not stored. | `test_retries_use_backoff_then_escalate_to_lee`, `test_a_timed_out_worker_cannot_write_later` |
| Stub acceptance run stalls at clarification | The Scottish Short-Term Let Host Kit brief is specific enough to proceed. The simulated harness runs that brief through the engine and scores the draft package. | `test_acceptance_brief_runs_end_to_end_simulated` |

Run the regression file:

```bash
python3 -m pytest apps/api/tests/test_qa_defects.py -q
```

On this machine that file passed (9 tests, including the two resume tests in `test_finish.py` when run with it). A wider `apps/api/tests` run passed 293 and failed 17. Those 17 need optional runtimes that are not installed here (Playwright, Chrome, crawl4ai, qwen-agent, pydantic-ai) or a live model. They are not the four defects above.

## Acceptance run

```bash
AYVEN_LLM_STUB=1 AYVEN_ENV=local python3 -c "from pathlib import Path; from validation.wf1 import run; print(run(Path('evidence/qa/wf1'), 'local')['verdict'])"
```

`run_type` is `SIMULATED`. The eight package files are synthetic stand-ins with the page counts from the brief. The real trial PDFs are not in this repo. Verdict files land in `evidence/qa/wf1/<run-id>/`. Nothing is published or sent.

## Checklist

Each row has one status: Implemented and verified, Implemented awaiting live verification, Blocked, or Unresolved defect.

| Row | Status | Evidence |
|---|---|---|
| R1 | Implemented and verified | `test_a_job_over_its_cap_halts`, `test_the_monthly_cap_refuses_a_new_job`. A goal with no budget uses the `$2` default cap. |
| R2 | Implemented and verified | `test_wf1_harness` records at least two tasks for the host-kit brief. |
| R3 | Implemented awaiting live verification | `validate_tool_call` and `test_archive_think_queries_and_media_cannot_execute` reject bad calls. A live run has not logged a tool call on every step. |
| R4 | Implemented awaiting live verification | `test_files_and_traces_survive_a_process_restart`. Turso is optional and was not created. |
| R5 | Implemented and verified | `test_wf1_harness` records at least two handovers. |
| R6 | Implemented and verified | `test_wf1_harness` keeps a rejected finding and records the fix. |
| R7 | Implemented and verified | `test_startup_resumes_a_killed_run_without_redoing_finished_work`, `test_resume_keeps_the_same_package_and_does_not_repeat_model_calls`, `test_a_running_job_resumes_after_the_worker_sleeps`. |
| R8 | Implemented and verified | `test_wf1_harness` (`completion_allowed` stays false until the files exist). |
| R9 | Implemented and verified | `test_r9_reasoning.py`: unclosed think, think inside JSON, harmony analysis, planning prose, `think.mp3`, empty content with a reasoning field. |
| R10 | Implemented awaiting live verification | `scripts/run_ayven_live.sh` prints the llama-server plan and exits 0. `load_s` is null until a model is loaded. No pod was created. |
| R11 | Implemented awaiting live verification | The API runs without a laptop. This branch is not the service Render is serving. |
| R12 | Implemented awaiting live verification | `test_a_repeated_submission_returns_the_same_project`. The Node harness was not run. |
| R13 | Implemented awaiting live verification | `GET /campus/view` returns blockers, files, and criteria. No browser pass on this turn. |
| R14 | Implemented awaiting live verification | `run_metrics` and `test_wf1_harness` cover the simulated fields. No GPU cost report exists. |
| R15 | Implemented awaiting live verification | `test_a_session_older_than_twelve_hours_is_refused`. The live `/health` check was not repeated, and this branch is not deployed. |
| R16 | Implemented awaiting live verification | `test_wf1_harness` passes with `run_type` SIMULATED. The GPU run was not done. |
| R17 | Implemented and verified | `test_escalation_disabled_makes_no_external_call`, `test_escalation_armed_without_prices_refuses_startup`. |

```bash
python3 -m pytest apps/api/tests/test_r9_reasoning.py apps/api/tests/test_hosting.py apps/api/tests/test_spend_controls.py apps/api/tests/test_qa_defects.py -q
bash scripts/run_ayven_live.sh
```

## Lee still has to do

- Deploy this branch. Setting the access key does not deploy this code.
- Create the Turso database if Campus should leave the SQLite file. Set both `AYVEN_DB_URL` and `AYVEN_DB_AUTH_TOKEN`. A URL without a token refuses to start.
- Keep `AYVEN_LLM_STUB=1` until a live model is intentional.
- GPU: read `docs/LIVE_TEST.md`. Do not set `AYVEN_CONFIRM_SPEND=YES` until the cap and the delete command are accepted.

Measured spend this run: £0. No external model call was made.
