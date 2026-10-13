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

## Checklist (honest)

| Row | Status | Note |
|---|---|---|
| R1–R6, R8, R14 | VERIFIED in code and unit paths | Goals, caps, handovers, QA reject/fix, completion gate, and per-run metrics exist. The simulated wf1 run exercises them. |
| R7 | VERIFIED | Restart re-dispatch covered by the tests named above. |
| R9 | VERIFIED for the tool and message paths in this tree | Reasoning fields are not copied into tool arguments. A live GPU leak test was not run. |
| R10, R11, R16 live, R17 live | AWAITING-LIVE | Scripts and the decision function are in the tree. No pod was started. |
| R15 | Lee reports `AYVEN_ACCESS_KEY` is set on Render | This agent got no response from `GET /health` within 20s, so the live flag was not re-checked here. This branch is not deployed. Non-local startup still refuses a short token. |
| R16 simulated | VERIFIED | Pass mark is the harness verdict, labelled SIMULATED. |
| R12, R13 | Not re-proven in a browser this run | Milo contract is `docs/MILO_CONTRACT.md`. |

## Lee still has to do

- Deploy this branch. Setting the access key does not deploy this code.
- Create the Turso database if Campus should leave the SQLite file. Set both `AYVEN_DB_URL` and `AYVEN_DB_AUTH_TOKEN`. A URL without a token refuses to start.
- Keep `AYVEN_LLM_STUB=1` until a live model is intentional.
- GPU: read `docs/LIVE_TEST.md`. Do not set `AYVEN_CONFIRM_SPEND=YES` until the cap and the delete command are accepted.

Measured spend this run: £0. No external model call was made.
