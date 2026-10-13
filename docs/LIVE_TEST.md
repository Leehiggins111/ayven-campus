# Live model test (not run)

No pod was created and no money was spent. The default command path prints the plan and exits. A pod is refused unless `AYVEN_CONFIRM_SPEND=YES`, which this run does not set.

## Model

Primary: `Qwen3-30B-A3B-Instruct-2507`, one model for every role, llama.cpp `llama-server`, GGUF `Q4_K_M` (bartowski, about 18.6 GB), 16K context, `--reasoning off`, `--jinja`.

Fallback A, only if llama.cpp will not load: vLLM AWQ `Sophia-AI/Qwen3-30B-A3B-Instruct-2507-AWQ-W4A16` with `--structured-outputs-config.backend xgrammar`. Do not pass `--reasoning-parser qwen3`. vLLM 0.12 removed `--guided-decoding-backend` and `guided_json`. `scripts/gpu_employee.sh` and `scripts/gpu_manager.sh` now use the structured-outputs flag. They still name the older 8B and 30B-A3B checkpoints and are not the live-test command.

Fallback B, only if the 2507 quality fails on the fixture: `gpt-oss-20b` on llama.cpp.

Grammar stays on the constrained schema path. Do not switch the tool call to native `tools=`.

Sampling stays the per-request temperature already in Campus (default 0.2). The model card's 0.7 is not forced.

## Caps

Community rate `$0.34/h`, hard cap `$1.10`. Secure rate `$0.89/h`, hard cap `$2.75`. Wall clock 3.0 hours. Idle stop after 600 seconds under 5% util with nothing in flight. Pod lifetime 3 hours. Startup timeout 2400 seconds. Delete the pod. Do not stop it: a stopped pod keeps the volume-disk charge.

`gpu_lifecycle.decide` stops on the first of: results downloaded, time, cap, idle, two failures or a health failure, or a manual stop. `delete_command` tries `runpodctl pod delete`, then `runpodctl remove pod`, then `DELETE https://rest.runpod.io/v1/pods/$ID`.

## Confirmed against RunPod docs on 13 Oct 2026

- `RUNPOD_POD_ID` and a pod-scoped `RUNPOD_API_KEY` are injected.
- Stop keeps `/workspace` and continues volume billing. Terminate deletes data that is not on a network volume.
- The current CLI verb in the manage-pods doc is `runpodctl pod delete`.

## Not confirmed

- Whether running out of prepaid balance stops a pod.
- Whether the account has a spend limit that would stop this pod.
- Which `runpodctl` verb is installed in the pod image. The command tries delete, then remove, then REST.
- Whether `nvidia-smi` utilisation is a RunPod guarantee. The idle rule uses it as a local signal only.

## Checklist rows that this document covers

| Row | Status | Evidence |
|---|---|---|
| R1 | Implemented and verified | Caps in `budget.py`. Tests: `test_a_job_over_its_cap_halts`, `test_the_monthly_cap_refuses_a_new_job`. |
| R2 | Implemented and verified | `test_wf1_harness` task count. |
| R3 | Implemented awaiting live verification | Tool gate tests pass. A live per-step tool log does not exist yet. |
| R4 | Implemented awaiting live verification | `test_files_and_traces_survive_a_process_restart`. No Turso database was created. |
| R5 | Implemented and verified | `test_wf1_harness` handovers. |
| R6 | Implemented and verified | `test_wf1_harness` reject and fix. |
| R7 | Implemented and verified | Resume tests in `test_qa_defects.py` and `test_a_running_job_resumes_after_the_worker_sleeps`. |
| R8 | Implemented and verified | `completion_allowed` in `test_wf1_harness`. |
| R9 | Implemented and verified | `test_r9_reasoning.py`. The six leak cases are CPU tests. No model was loaded, so `load_s` is unmeasured. |
| R10 | Implemented awaiting live verification | `bash scripts/run_ayven_live.sh` prints the plan and refuses a pod. `record_load` stores `load_s` when the pod supplies clocks. |
| R11 | Implemented awaiting live verification | Not deployed from this branch. |
| R12 | Implemented awaiting live verification | Idempotent `POST /projects` in `test_a_repeated_submission_returns_the_same_project`. |
| R13 | Implemented awaiting live verification | Campus view fields exist. No browser pass. |
| R14 | Implemented awaiting live verification | Simulated metrics exist. The GPU `cost-report.json` does not. |
| R15 | Implemented awaiting live verification | Session TTL test. Live health not re-checked. |
| R16 | Implemented awaiting live verification | Simulated harness passes. GPU run not done. |
| R17 | Implemented and verified | Escalation stays off, and startup refuses armed escalation with no prices. |

## What a pass would not prove

A simulated pass (`validation/wf1.py`, `run_type=SIMULATED`) proves the harness, the gates, and the stub engine. It does not prove model quality. The package files in that run are synthetic text with the brief's page counts, not the trial PDFs.

If the model server is down, jobs pause with a blocker. They are not marked successful. Work already in the database survives a laptop shutdown. A Render disk restart needs `AYVEN_RESUME=1` and a database that is not only the ephemeral container disk (SQLite on a disk, or Turso when both `AYVEN_DB_URL` and `AYVEN_DB_AUTH_TOKEN` are set).
