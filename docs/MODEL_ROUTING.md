# Model routing

Benchmark defaults remain available and are no longer the only assumption:

| Role | Env | Benchmark default |
| --- | --- | --- |
| Employee commentary | AYVEN_EMPLOYEE_MODEL | Qwen/Qwen3-8B |
| Supervisor audit commentary | AYVEN_SUPERVISOR_MODEL | Qwen/Qwen3-32B |
| Manager commentary | AYVEN_MANAGER_MODEL | Qwen/Qwen3-30B-A3B |
| Escalation | AYVEN_ESCALATION_MODEL | unset |

The registry (`intelligence/registry.py`) records provider, local versus remote, context, reasoning, tools, coding, relative cost, speed, memory, quantisation, and which tasks the model is for.

## Routes

| Work | Route |
| --- | --- |
| Arithmetic, door scenarios | `ayven-calculator`. No model |
| Extraction and evidence-bounded commentary | Employee role |
| Verification and the independent audit | Supervisor role |
| Planning commentary and the manager decision | Manager role |
| Unresolved work that would need a frontier model | Escalation route, **disabled** unless `AYVEN_ALLOW_ESCALATION=1` and a key is set |

`AYVEN_ALLOW_ESCALATION=0` is the default. With that value, `complete_role("ESCALATION")` returns a disabled message and does not call `api.x.ai` or any other paid URL, even if a key is present in the environment. Workforce roles never use the frontier key. They use a local OpenAI-compatible server (`AYVEN_LOCAL_LLM_BASE_URL`) or the stub.

The GPU harness loads one role at a time so the 8B, 32B Q4, and 30B-A3B Q4 are not resident together. Published findings still come from the ledger if the model disagrees.

Status: routing and the disable switch are **REAL**. Which local checkpoint is loaded is **NOT YET VERIFIED ON GPU** in this revision. Stub commentary is **STUBBED**.
