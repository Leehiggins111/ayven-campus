# Live model test (not run)

No pod was created and no money was spent. The default command path prints the plan and exits. A pod is refused unless `AYVEN_CONFIRM_SPEND=YES`, which this run does not set.

## Acceptance criteria

These are the gates. They are written to `evidence/live/<run-id>/criteria.json` before any result file. This run did not create that directory.

- 50 constrained tool calls per role: at least 98 percent valid the first time, 100 percent after one repair, and zero reasoning strings reaching a tool.
- 20 irrelevance prompts: at most 2 tool calls.
- 10 missing-tool prompts: at least 8 of 10 end in cannot or escalate.
- 10 director plans: at least 8 of 10 valid. `director_plans_pass` enforces that mark. A set that is not 10 plans fails.
- QA planted-defect set of 10: at least 8 caught.
- Record model identity, configuration, results, failures, run time, tokens, cost, and owner interventions under `evidence/live/<run-id>/`.

The multi-step task that exposes the long-chain weakness: search once for a public page about Scottish short-term let guest records, fetch that page, calculate 9.00 minus 2.98 as a check rather than a price, write one sentence that uses only a sentence from the page, then stop. Tools are `web_search`, `fetch_page`, and `calculator`. Code checks the calculator. The sentence must not add a claim that was not on the page.

## Model

Primary, step 1: `Qwen3-30B-A3B-Instruct-2507`, one model for every role, llama.cpp `llama-server`, GGUF `Q4_K_M` (bartowski, about 18.6 GB), 16K context, `--reasoning off`, `--jinja`. UNVERIFIED. Off until `AYVEN_CONFIRM_SPEND=YES`.

## Backup order

UNVERIFIED. Off by default. No paid external model. BFCL figures came from DashScope's API with native function calling. They do not transfer directly to Q4_K_M on llama.cpp. That comparison is an assumption.

1. Test step 1.
2. If the director plan test fails, try `Qwen3-8B` in BF16 with thinking on (about 16 GB, fits a 24 GB 4090). R9 strip tests must pass on that thinking output. Community 4090 was $0.34/h at 11:30; a 3090 was $0.22/h.
3. Only then `Qwen3-32B` Q4_K_M, 19.8 GB. On 24 GB that fits about 8K context. Otherwise a 48 GB A6000 at $0.33/h community. The live script does not start step 3.

Steps 1 and 2 run in one session under the same cap ($1.10 community, $0.34/h, or the secure cap if that tier is selected). Wall clock 3 hours. Delete the pod. Do not stop it.

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
| R2 | Implemented awaiting live verification | Scripted harness task count. Not a model run. |
| R3 | Implemented awaiting live verification | Tool gate tests pass. A live per-step tool log does not exist yet. |
| R4 | Implemented awaiting live verification | `test_files_and_traces_survive_a_process_restart`. No Turso database was created. |
| R5 | Implemented awaiting live verification | Scripted handovers. Not a model run. |
| R6 | Implemented awaiting live verification | Scripted reject-and-fix. Not a model run. |
| R7 | Implemented awaiting live verification | Lease, fence, and outbox tests. Not a live host restart. |
| R8 | Implemented awaiting live verification | Scripted completion gate. Not a model run. |
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
