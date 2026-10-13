# Handover

Branch: `cursor/ayven-workforce-fe56`. Base: `cursor/ayven-v2-workable-a9eb`. Draft PR #6. This branch supersedes the recovery design in open PRs #2 and #5. Those PRs are not closed and not merged.

Milo calls Campus at `CAMPUS_BASE_URL`. `AYVEN_ENGINE_TOKEN` equals `AYVEN_ACCESS_KEY`. Leave `AYVEN_MILO_WRITE_SECRET` unset. `MILO_ENGINE_ENABLED=1`. The access key must be at least 32 characters. No deploy, no pod, no spend.

## Recovery

One design. DBOS keeps the stage checkpoints from PRs #2 and #5. A local SQLite lease (`job_leases`) and an outbox (`side_effects`) cover the gaps those PRs left open: two workers, a late write, and a repeated tool call. Claims use `BEGIN IMMEDIATE` on the local file, with WAL and `busy_timeout`. They do not use Turso concurrent writes. `AYVEN_DURABLE=1` is the only recovery path when it is on. Otherwise `AYVEN_RESUME=1` claims, then resumes. Startup recovery is a background thread, so `/health` is not blocked.

| Test | Proves | Does not prove |
|---|---|---|
| `test_two_processes_cannot_claim_the_same_job` | Two live processes cannot both hold one claim. | DBOS dedupe across two API processes. |
| `test_a_restart_reads_the_durable_checkpoint_and_does_not_repeat_the_action` | A second process returns the stored stage receipt and does not run the action. | A live model call, or an external HTTP call. `test_durable.py` is the process-kill proof for the DBOS workflow. |
| `test_model_retries_go_through_complete` | A timeout is retried inside `_complete`. A returned tool error is not retried again on top of the research retry. | A live model timeout, or jitter. |
| `test_a_superseded_fence_cannot_commit` | The fencing token is checked at commit. A superseded update is rolled back. | An operating-system thread that was killed. |
| `test_a_recorded_side_effect_is_not_sent_again` | The tool function runs once. A pending row is not sent again. | That a remote server saw only one request if the process died after sending and before the receipt. |
| `test_durable_mode_does_not_also_thread_resume` | Thread resume stays idle when durable mode is on. | That DBOS launched. |
| `test_health_answers_while_recovery_is_blocked` | `/health` returns while recovery is blocked. | That a job was recovered. |

Earlier rows that cited the scripted `validation/wf1.py` harness as verified were wrong. That harness is `SIMULATED`. It does not prove model quality. R7 was not verified by the older resume tests: those did not exclude a second live worker.

## Orchestration

The director's owned tasks are the coordination record. `start_task` refuses a step whose dependency is not done. The employee prompt contains that one step, its acceptance criteria, and its tool allowlist. Arguments go through `validate_tool_call`. A finished step writes the next handover. Timeouts and retries for a step use `run_bounded` and `run_retries`, the same runners as the model call. The model does not choose the next owner.

## Milo contract

`POST /milo/jobs` stores `constraints` and gives them to the director and to QA as hard constraints. `GET /milo/jobs/{id}` includes `mode` and `constraints`. Mode is `SIMULATED` while the stub is on. A missing mode is `UNVERIFIED`. The same idempotency key with a different title is the same job. A different objective with that key is 409. A new key is a new job.

## Checklist

| Row | Status | Evidence |
|---|---|---|
| R1 | Implemented and verified | Local cap tests in `test_spend_controls.py`. Not a live GPU charge. |
| R2 | Implemented awaiting live verification | Scripted harness task count. Not a model run. |
| R3 | Implemented awaiting live verification | Tool gate tests. No live per-step tool log. |
| R4 | Implemented awaiting live verification | `test_files_and_traces_survive_a_process_restart`. No Turso database was created. |
| R5 | Implemented awaiting live verification | Scripted handovers. Not a model run. |
| R6 | Implemented awaiting live verification | Scripted reject-and-fix. Not a model run. |
| R7 | Implemented awaiting live verification | Lease, fence, outbox, and durable receipt tests above. Not a live host restart. |
| R8 | Implemented awaiting live verification | Scripted completion gate. Not a model run. |
| R9 | Implemented and verified | `test_r9_reasoning.py` on CPU. No model was loaded, so `load_s` is unmeasured. |
| R10 | Implemented awaiting live verification | `bash scripts/run_ayven_live.sh` prints the plan and exits 0. No pod. |
| R11 | Implemented awaiting live verification | This branch is not deployed. Render is still the old build. |
| R12 | Implemented awaiting live verification | `test_milo_contract.py` and `test_a_repeated_submission_returns_the_same_project`. The Node harness was not run. |
| R13 | Implemented awaiting live verification | Campus view fields exist. The reject control must send a reason. No browser pass on this turn. |
| R14 | Implemented awaiting live verification | No GPU cost report. |
| R15 | Implemented awaiting live verification | Session TTL test. This branch is not deployed. |
| R16 | Implemented awaiting live verification | Simulated harness. The GPU run was not done. |
| R17 | Implemented and verified | Escalation stays off. Startup refuses armed escalation with no prices. |

Focused run: `test_steps.py`, `test_milo_contract.py`, `test_leases.py`, `test_durable.py`, and `test_release_handoff.py` passed. A wider `apps/api/tests` run, excluding browser tests, was 324 passed and 13 failed. The 13 are missing optional runtimes (Playwright, crawl4ai, qwen-agent), not the contract or lease tests. Do not use the older "293 passed, 17 failed" figure.

## Backup model

UNVERIFIED. Off by default. No paid external model.

1. Test `Qwen3-30B-A3B-Instruct-2507`, GGUF Q4_K_M, 16K, `--reasoning off`.
2. If the director plan test fails, `Qwen3-8B` BF16 with thinking on (about 16 GB, a 24 GB 4090 at the 11:30 community price of $0.34/h). R9 must pass on that thinking output.
3. Only then `Qwen3-32B` Q4_K_M (19.8 GB). On 24 GB that is about 8K context. Otherwise a 48 GB A6000 at $0.33/h community. The live script does not start step 3.

BFCL figures came from DashScope's API with native function calling. Using them for Q4_K_M on llama.cpp is an assumption. Community prices noted at 11:30: 3090 $0.22, 4090 $0.34.

Steps 1 and 2 are one session under the community cap ($1.10, $0.34/h). `bash scripts/run_ayven_live.sh` without `AYVEN_CONFIRM_SPEND=YES` prints the plan and exits 0.

## Deferred

Rate limit on `/session`, backoff jitter, a bounded attempt log, UI copy beyond the mode flag, trusted proxy headers for the Secure cookie, keeping the read token off `/admin/backup` by a further check, rewriting the isolation test, and changing `atomic_stage` so it does not hold the write lock across a model call. `render.yaml` was not changed.

## Lee still has to do

- Deploy this branch. An access key does not deploy it.
- Set `CAMPUS_BASE_URL`, `AYVEN_ACCESS_KEY` (32 characters or more), and `AYVEN_ENGINE_TOKEN` to that same key. Leave `AYVEN_MILO_WRITE_SECRET` unset.
- Read `docs/LIVE_TEST.md` before any GPU run. The only remaining spend step is `AYVEN_CONFIRM_SPEND=YES` on a pod with a key. This run did not set it.

```bash
python3 -m pytest apps/api/tests/test_milo_contract.py apps/api/tests/test_leases.py apps/api/tests/test_durable.py apps/api/tests/test_qa_defects.py -q
bash scripts/run_ayven_live.sh
```

Measured spend this run: £0. No external model call was made.
