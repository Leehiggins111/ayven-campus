# Model roles

v1.0.0 routes by task. The table is the benchmark default, not a fixed assignment. See `MODEL_ROUTING.md`. Calculation uses the deterministic calculator. Escalation is off unless `AYVEN_ALLOW_ESCALATION=1`.

| Role | Env | Default | Why |
|---|---|---|---|
| EMPLOYEE | AYVEN_EMPLOYEE_MODEL | Qwen/Qwen3-8B | Official ~8B dense, closest to requested 9B class. Apache-2.0. |
| SUPERVISOR | AYVEN_SUPERVISOR_MODEL | Qwen/Qwen3-32B | No official 27B; 32B is current official review-class dense. |
| MANAGER | AYVEN_MANAGER_MODEL | Qwen/Qwen3-30B-A3B | Official MoE (~3B active). |
| ESCALATION | AYVEN_ESCALATION_MODEL | unset | Only if AYVEN_ALLOW_ESCALATION=1 and a key is set. |

Serve via llama.cpp, Ollama, vLLM or SGLang at AYVEN_LOCAL_LLM_BASE_URL. Render hosts the app, not the weights.
