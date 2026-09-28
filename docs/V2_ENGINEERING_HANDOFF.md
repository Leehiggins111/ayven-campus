# Ayven v2 engineering handoff

This is the state of the tree after the local control-plane build. It is not a claim that the three frozen exams pass on Qwen.

## FINAL SHA

The SHA is the commit that contains this file. Read it from `git rev-parse HEAD` on the branch that was pushed. Do not trust a SHA typed here before that commit exists.

## VERSION

2.0.0. `GPU_VALIDATED` is false. `VALIDATION_STATUS` is `V2_LOCAL_CONTROL_PLANE_GPU_PENDING`.

## ARCHITECTURE CHANGES

Intelligence and control are split.

Models may interpret, plan, and propose. The runtime executes a call only after `validate_tool_call` accepts a Pydantic object taken from the executable channel. The reasoning channel is discarded and is not stored as a tool argument, claim, memory row, or customer sentence.

Work packages, employees, the supervisor, the manager, approvals, Campus, and SQLite remain the product. Open-source projects are equipment behind those boundaries.

New control pieces that run on the production path (`execution.run_objective`, which the API calls): entity targets, authority class, rerank scores, noise rejection, repair after a disproved claim, employee self-check, completion contract, workflow state, and an approval row when escalation still needs a person.

## GPU FAILURE ROOT CAUSES

The run archive is not in the repo. See `docs/V2_GPU_FAILURE_ANALYSIS.md`.

The 1.1.1 mechanisms that match the reported symptoms:

1. Tool lines were parsed before reasoning was removed, so `<think>` text could become a query or URL (`think`, `think.mp3`).
2. A regex strip after generation was the only defence.
3. Research opened hits without an entity plan or a noise filter.
4. `ESCALATE` returned before the approval insert.
5. Vending could be safe and still PARTIAL because usefulness is not the same as refusal.
6. `CONTRADICTED` claims did not increment retries. Only a supervisor `RETURN` did.

The 24 claim rows themselves were not available. They were not reconstructed.

## COMPONENT MATRIX

| Piece | State | What executes |
| --- | --- | --- |
| Typed boundary | ACTIVE | Every tool call |
| llguidance | ACTIVE when installed (it is in requirements) | Grammar mask and `response_format` on the local OpenAI path |
| Qwen-Agent | ACTIVE when imported | Employee tool loop, after the gate |
| Pydantic runtime | ACTIVE as a schema runtime, not Pydantic AI | Optional `AYVEN_AGENT_RUNTIME=pydantic` |
| Research v2 | ACTIVE | Targets, filter, authority, rerank |
| Crawl4AI | OPTIONAL_NOT_INSTALLED | Ladder hook only |
| Browser Use | ACTIVE when Chrome and the package exist | JavaScript / interaction only |
| Claim ledger v2 | ACTIVE | Extra columns on `claims` |
| Repair loop | ACTIVE | Capped, local |
| Self-check | ACTIVE | Stored before the supervisor |
| Supervisor | ACTIVE | Checks plus repair |
| Manager | ACTIVE | Safety veto remains |
| Approval state machine | ACTIVE | `workflow_state` plus approval rows |
| Memory | ACTIVE | SQLite |
| Mem0 | REJECTED | — |
| Model gateway | ACTIVE | `gateway.py` |
| LiteLLM | REJECTED | — |
| DSPy | OPTIONAL | Offline candidate only |
| MCP | ACTIVE when the SDK and servers are configured | Registry, not every employee |
| Code sandbox | ACTIVE only with unshare or bubblewrap, allow flag, and approval | Otherwise reported and idle |
| Documents | ACTIVE for text, CSV, HTML | PDF unavailable |
| Coding specialist | ACTIVE contract, sandbox engine | One specialist |
| Observability | ACTIVE | Package JSON and optional trace file |
| Langfuse | OPTIONAL | Not installed |
| Security fences | ACTIVE | Tests |
| llm-guard | OPTIONAL | Not installed |
| Garak | OPTIONAL | Script only |
| Result export | ACTIVE gate | VERIFIED only with a configured remote |

## OSS INTEGRATED

llguidance 1.3.0, Pydantic 2.13.5, Qwen-Agent 0.0.34 (when installed), Browser Use 0.13.10 (when installed), MCP SDK 2.1.1 (when installed), FastAPI, HTTPX.

## OSS ADAPTED

Pydantic AI’s ideas, without the framework. Bubblewrap when `unshare` is absent. The research ladder’s shape, without Crawl4AI installed.

## OSS REJECTED

Mem0, LiteLLM, Letta, Graphiti (deferred rather than rejected as a bad idea; it is not integrated), OpenHands, Aider, SWE-agent, mini-SWE-agent, smolagents, LangGraph, Firecrawl (AGPL), PyMuPDF (AGPL), Daytona, Rebuff. Reasons are in `docs/INTELLIGENCE_COMPONENTS.md`.

## TYPED TOOL BOUNDARY STATUS

ACTIVE. `extract_executable` never reads the reasoning channel. `validate_tool_call` is required before `AyvenTool.call` runs a payload.

## LLGUIDANCE STATUS

ACTIVE in this environment (1.3.0). `first_token_is_schema` allows byte 123 (`{`) and rejects `<`. The local OpenAI-compatible caller sends `response_format` json_schema. If a server ignores the grammar, the same grammar still rejects the text before a tool runs.

## PYDANTIC AI DECISION

ADAPTED, not adopted. Ayven is not a Pydantic AI app. Schemas, structured decisions, and approval records are Ayven’s.

## QWEN-AGENT STATUS

Kept. `QwenAgentRuntime` is selected when the package imports. Failures fall back to native text. The gate still applies.

## RESEARCH v2 STATUS

ACTIVE. Entity targets are stored. Noise URLs are not opened. Model-originated hits below 0.2 rerank are not fetched. Explicit test queries are still opened so a deliberately irrelevant fixture can be labelled. Coverage from a real reviewer stops further searches. The stub reviewer is not treated as coverage.

## CRAWL4AI STATUS

OPTIONAL_NOT_INSTALLED. Live HTTP text shorter than 40 characters calls the adapter, which returns not-installed and does not invent a page.

## BROWSER USE STATUS

Unchanged role: read-only, after HTTP fails a JavaScript wall or interaction is required. Not a search engine.

## ENTITY RESOLUTION STATUS

ACTIVE and generic. Capitalised names become targets. Public-body vs official is inferred from ordinary words in the objective (council, hospital, ticket), not from a list of exam organisations.

## RERANKING STATUS

ACTIVE. Token overlap plus an authority bonus. Scores are stored on hits. No embedding model.

## CLAIM LEDGER STATUS

ACTIVE. Columns: origin, contradicting evidence, authority, repair history, verification history. Recommendations stay a claim type and are not treated as facts by the self-check.

## REPAIR LOOP STATUS

ACTIVE. Cap `AYVEN_MAX_REPAIRS` (default 3). Actions include REMOVE_CLAIM, RECALCULATE, RESEARCH_MORE, and UNRESOLVED_GAP. A repaired claim is `UNVERIFIED`, not left published as supported. Retries increment.

## EMPLOYEE SELF-CHECK STATUS

ACTIVE. Stored as `employee_self_check` before the supervisor. It is a checklist. The supervisor prompt still does not receive employee reasoning.

## SUPERVISOR STATUS

ACTIVE. Independent calculator and page checks remain. Disproved claims go through repair before a leftover contradiction is stored.

## MANAGER STATUS

ACTIVE. Decisions and the safety veto are unchanged in rank. `ESCALATE` no longer skips the approval row when the package requires a person.

## APPROVAL STATE MACHINE STATUS

ACTIVE. States are the list in `workflow.py`. Illegal transitions are recorded as forced rather than dropped. `AWAITING_APPROVAL` is set when an approval row is created. Resolving that approval moves the package to `APPROVED` or `FAILED`. Approval does not send an external action. Tests cover the escalation path and the resolve path.

## MEMORY STATUS

ACTIVE. SQLite scopes, provenance, confidence, supersedes, threshold. Reasoning-only text is refused.

## MEM0 DECISION

REJECTED. Local overlap ranking is enough, and Mem0’s gain needs vectors we are not downloading.

## MODEL ROUTER STATUS

ACTIVE. `gateway.py` selects from the existing registry, records cooldown, and prices local roles at zero token-cost. GPU time is still the harness estimate. Defaults remain 8B / 32B / 30B-A3B.

## LITELLM DECISION

REJECTED. Split licence, large install, no local capability the gateway lacks.

## DSPY / OFFLINE OPTIMISATION STATUS

OPTIONAL. `propose()` returns `approved: false`. Nothing on the request path loads DSPy or edits a prompt.

## MCP v2 STATUS

The pinned SDK is 2.1.1 (the browser-use pin). Status reports stdio and Streamable HTTP. Discovery still comes from `AYVEN_MCP_SERVERS`. Action tools stay approval-gated. Not every tool is mounted on every employee.

## CODE SANDBOX STATUS

Unchanged execution rule: unshare or bubblewrap, or the call does not run. Daytona is rejected. gVisor, nsjail, and Firecracker are optional and absent. rlimit is not a production sandbox and is not used.

## DOCUMENT INTELLIGENCE STATUS

ACTIVE for UTF-8 text, CSV, and HTML, with source name on the result. PDF returns `pdf_extractor_not_installed`. PyMuPDF is not imported.

## SPECIALIST ENGINE STATUS

`run_coding` calls the sandbox and returns evidence for a supervisor. OpenHands, Aider, and SWE-agent are not installed. Status is not_applicable unless the task class is software engineering.

## OBSERVABILITY STATUS

The parent package JSON now includes repairs, self-checks, the contract, targets, and relevant-open counts. `AYVEN_TRACE_PATH` appends JSON lines. Langfuse is not required.

## SECURITY STATUS

System, objective, observation, and web are separate strings. Web text is fenced. Injection phrases are detected and do not replace the objective. Rebuff is not used. llm-guard is not installed.

## GARAK STATUS

OPTIONAL. `scripts/run_security_eval.sh` exits 0 unless `AYVEN_RUN_GARAK=1` and a local base URL is set. It is not in pytest.

## DEPENDENCY HEALTH

`apps/api/app/intelligence/deps.py` and `apps/api/constraints.txt`. Core requirements now pin llguidance 1.3.0. Known risk: installing llama-cpp or browser-use without the constraints file can move `typing-extensions` or `click`. The banner prints versions and that warning. Preflight on a GPU aborts if llguidance is inactive or core imports are missing.

## RESULT PERSISTENCE STATUS

`validation/export_results.py`. A local tar is not VERIFIED. Git, S3, rclone, or a Hugging Face dataset must be configured and read back. Otherwise the harness and the shell print `DO NOT STOP POD — RESULTS NOT EXPORTED`. No model weights are committed.

## CAMPUS STATUS

The served Campus (`apps/api/static/r3f/campus-app.jsx`) shows doing, why, stage, skills, stuck, needs you, finished, and trust from `GET /state` `campus_brief`. Traces stay in the intelligence endpoints. The React source in `apps/campus` has the same summary. A WebGL failure is caught so the status panel still renders. An approval on that panel is the same `POST /approvals/{id}/resolve` path. After approval the stage is `APPROVED` and "needs you" is no.

## PRODUCTION WORKFLOW STATUS

`POST /projects` still calls `orchestrator` → `workforce.run_objective` → `execution.run_objective`. The same function is what the harness binds. There is no second exam engine.

## TEST COUNT

73 passed, 0 failed. Command: `PYTHONPATH=. python3 -m pytest tests -q` from `apps/api`. Warnings only: FastAPI `on_event` deprecation and a dashscope assistants deprecation from qwen-agent.

## TEST RESULTS

Local pytest was run with `AYVEN_LLM_STUB=1`, `AYVEN_ALLOW_ESCALATION=0`, and fixture research. Result: **73 passed**. No GPU and no paid API. Frozen exam files were not edited.

## UNSEEN EVAL RESULTS

`benchmarks/unseen/tasks.json` plus `tests/test_unseen.py`. Checks are behavioural: ambiguous work stays inside a manager decision, contradictory fixture values are both kept, an empty search does not fall back to memory, and a page that says “ignore previous instructions” does not become the system prompt. This does not measure Qwen.

## KNOWN LIMITATIONS

- Crawl4AI, Mem0, LiteLLM, DSPy, Langfuse, Garak, llm-guard, and a PDF parser are not installed.
- The repair’s `RESEARCH_MORE` records a question and demotes the claim. It does not launch a second full research programme inside the supervisor.
- Workflow transitions that the table forbids are stored as forced rather than crashing a package. That is visible in the verification row.
- Relevance filtering is strict for model-originated queries only. An operator-supplied query can still open a weak page and label it irrelevant.
- Entity extraction is capitalisation and keywords. It will miss lowercase names and will split awkwardly on titles.
- The byte-level llguidance tokenizer proves the grammar. A live llama.cpp process must be given `llguidance_grammar` by the server for the mask to apply during sampling. The post-check still refuses a bad string.
- Campus 3D still loads React from a CDN. The new panel is in the page source.

## WHAT REMAINS UNPROVEN WITHOUT REAL MODELS

Whether Qwen 8B/32B/30B will satisfy the frozen exams, whether research precision rises on live pages, whether the repair rate on the next GPU run is above zero, and whether the manager’s own rationale is useful. The control plane can reject garbage. It cannot supply the missing judgement.

Label: **SYSTEM VERIFIED** for the local tests. **MODEL INTELLIGENCE UNVERIFIED**.

## FRANKENSTEIN READINESS

PARTIAL. The runtime hooks are in place and local integration tests pass. A GPU run has not been executed for 2.0.0, so this is not READY.

## EXACT GPU COMMAND

From the repo root, on a machine that already has a GPU. This command does not rent one.

```bash
export AYVEN_ALLOW_ESCALATION=0
export AYVEN_GPU_USD_PER_HOUR=2.00
# optional durable export — without one of these, do not stop the pod
# export AYVEN_EXPORT_S3_URI=s3://your-bucket/ayven
# export AYVEN_EXPORT_RCLONE_TARGET=remote:ayven
# export AYVEN_EXPORT_GIT_REMOTE=... AYVEN_EXPORT_GIT_BRANCH=ayven-results
bash scripts/run_ayven_validation.sh --preflight-only   # abort before download if core is down
bash scripts/run_ayven_validation.sh
```

The script prints VERSION, SHA, GPU, VRAM, DISK, DEPENDENCY HEALTH, CORE COMPONENT STATUS, and RESULT EXPORT STATUS before `validation/prepare.py` downloads weights.

## EXPECTED GPU COST

Estimate only, not a quote. The preserved run was about 631 seconds and about $0.35 of inference. At the script’s default $2.00 per GPU-hour that is about $0.35 of GPU time as well. v2 can add a small number of repair searches. A planning range is **$0.50 to $2.00** if the run stays under an hour at $2/hour. If the pod is left up after the script finishes, the cost is whatever the host bills, which is why the script refuses to print STOP POD until export is verified.

## CONFIRM

- No GPU was started by this build.
- `AYVEN_ALLOW_ESCALATION` remains 0. No paid API key is required and none is enabled in code.
- `render.yaml` was not changed. Nothing was deployed.
- Frozen exams under `benchmarks/exams` were not edited. `git diff 77284e3 -- benchmarks/exams` is empty (confirmed at commit time).
- Exam entity names are still banned in the engine by `test_engine_source_does_not_name_exam_entities`.
