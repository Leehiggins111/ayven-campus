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

Read `docs/V2_REAL_GPU_FORENSICS.md`. It is the archive `ayven-validation-20260928T102746Z`. `docs/V2_GPU_FAILURE_ANALYSIS.md` now points there and no longer says the artifacts were missing.

Headline: verdict FAIL. 131 claims created and challenged. Scorecard rejected **24** = 18 `CONTRADICTED` + 6 `unsupported_removed`. Supervisor payloads were 116 STOOD and 15 DISPROVED. Status counts were 89 SUPPORTED, 22 UNVERIFIED, 18 CONTRADICTED, 2 PARTIALLY_SUPPORTED. Retries 0. Approvals 0. Three searches ran the query `<think>`. Planning paragraphs were also searched. `Door: £82` was stripped even though the brief states it. Four unopened club URLs were stripped and not repaired. Vending stayed PARTIAL.

## COMPONENT MATRIX

Each piece is exactly one of: ACTIVE AND EXECUTED, ACTIVE BUT NOT MODEL-VALIDATED, OPTIONAL, DEFERRED, FAILED, REJECTED.

| Piece | Class |
| --- | --- |
| Typed boundary | ACTIVE AND EXECUTED |
| llguidance post-check | ACTIVE AND EXECUTED |
| Qwen-Agent live weights | ACTIVE BUT NOT MODEL-VALIDATED |
| Pydantic AI runtime (FunctionModel through the gate) | ACTIVE AND EXECUTED |
| Research filter, rank, entity repair | ACTIVE AND EXECUTED |
| Crawl4AI markdown on fetched or local HTML | ACTIVE AND EXECUTED |
| Browser Use local HTTP read | ACTIVE AND EXECUTED |
| Claim ledger | ACTIVE AND EXECUTED |
| Repair loop | ACTIVE AND EXECUTED |
| Employee self-check | ACTIVE AND EXECUTED |
| Supervisor checks | ACTIVE AND EXECUTED |
| Manager safety veto | ACTIVE AND EXECUTED |
| Approval state machine | ACTIVE AND EXECUTED |
| Semantic memory (fastembed, lexical fallback) | ACTIVE AND EXECUTED |
| Mem0 | REJECTED |
| Local model gateway | ACTIVE AND EXECUTED |
| LiteLLM as Ayven's gateway | REJECTED |
| DSPy | OPTIONAL |
| MCP registry | ACTIVE AND EXECUTED |
| Code sandbox (unshare or bubblewrap) | ACTIVE AND EXECUTED |
| Documents PDF, DOCX, XLSX | ACTIVE AND EXECUTED |
| Coding specialist beyond the sandbox | REJECTED |
| Observability traces | ACTIVE AND EXECUTED |
| Langfuse | OPTIONAL |
| Security fences and injection page | ACTIVE AND EXECUTED |
| llm-guard | OPTIONAL |
| Garak | OPTIONAL |
| Git result export (local bare repo read-back) | ACTIVE AND EXECUTED |
| Graphiti | DEFERRED |
| Firecrawl, PyMuPDF | REJECTED |
| Daytona, OpenHands, Aider, SWE-agent, mini-SWE, Letta, Rebuff | REJECTED |
| Live llama.cpp grammar mask during sampling | ACTIVE BUT NOT MODEL-VALIDATED |
| Qwen exam quality | ACTIVE BUT NOT MODEL-VALIDATED |

## OSS INTEGRATED

llguidance 1.3.0, Pydantic 2.13.5, Qwen-Agent 0.0.34 (when installed), Browser Use 0.13.10 (when installed), MCP SDK 2.1.1 (when installed), FastAPI, HTTPX.

## OSS ADAPTED

Pydantic AI 2.51.0 runs as an optional runtime through Ayven's gate. Crawl4AI 0.7.4's markdown generator runs on HTML. Bubblewrap is the sandbox when `unshare` is absent.

## OSS REJECTED

Mem0, LiteLLM, Letta, Graphiti (deferred rather than rejected as a bad idea; it is not integrated), OpenHands, Aider, SWE-agent, mini-SWE-agent, smolagents, LangGraph, Firecrawl (AGPL), PyMuPDF (AGPL), Daytona, Rebuff. Reasons are in `docs/INTELLIGENCE_COMPONENTS.md`.

## TYPED TOOL BOUNDARY STATUS

ACTIVE. `extract_executable` never reads the reasoning channel. `validate_tool_call` is required before `AyvenTool.call` runs a payload.

## LLGUIDANCE STATUS

ACTIVE in this environment (1.3.0). `first_token_is_schema` allows byte 123 (`{`) and rejects `<`. The local OpenAI-compatible caller sends `response_format` json_schema. If a server ignores the grammar, the same grammar still rejects the text before a tool runs.

## PYDANTIC AI DECISION

ACTIVE AND EXECUTED as an optional runtime, not the default employee. pydantic-ai-slim 2.51.0 is installed. `PydanticRuntime.employee_turn` runs a `FunctionModel` and a `TestModel`, then `validate_tool_call`. A reasoning-wrapped tool line is rejected. Ayven still owns packages, approvals, and the ledger.

## QWEN-AGENT STATUS

Kept. `QwenAgentRuntime` is selected when the package imports. Failures fall back to native text. The gate still applies.

## RESEARCH v2 STATUS

ACTIVE. Entity targets are stored. Noise URLs are not opened. Model-originated hits below 0.2 rerank are not fetched. Explicit test queries are still opened so a deliberately irrelevant fixture can be labelled. Coverage from a real reviewer stops further searches. The stub reviewer is not treated as coverage.

## CRAWL4AI STATUS

ACTIVE here. `crawl4ai` 0.7.4 `DefaultMarkdownGenerator` extracts HTML, including a `file://` page used by the test. Live or HTML fetches on the ladder call it. LiteLLM is imported by Crawl4AI and is not Ayven's gateway.

## BROWSER USE STATUS

Unchanged role: read-only, after HTTP fails a JavaScript wall or interaction is required. Not a search engine.

## ENTITY RESOLUTION STATUS

ACTIVE and generic. Capitalised names become targets. Public-body vs official is inferred from ordinary words in the objective (council, hospital, ticket), not from a list of exam organisations.

## RERANKING STATUS

ACTIVE. Token overlap plus an authority bonus. Scores are stored on hits. No embedding model.

## CLAIM LEDGER STATUS

ACTIVE. Columns: origin, contradicting evidence, authority, repair history, verification history. Recommendations stay a claim type and are not treated as facts by the self-check.

## REPAIR LOOP STATUS

ACTIVE. Cap `AYVEN_MAX_REPAIRS` (default 3). `RESEARCH_MORE` and `REPLACE_SOURCE` run a bounded `research()` call (search, filter, fetch, extract). If the opened page supports the claim and the supervisor recheck does not disprove it, the ledger status becomes `SUPPORTED` and only that section is rewritten. `RECALCULATE` uses the calculator. `REMOVE_CLAIM`, `DOWNGRADE_TO_INFERENCE`, and `REWRITE` change the ledger and the section. A repair that finds nothing stays `UNVERIFIED` and is not counted as corrected. Retries increment. Repair rate is corrected / rejected.

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

REJECTED. Mem0's default embedder is a hosted call and it would be a second memory server. Retrieval uses local fastembed `BAAI/bge-small-en-v1.5` when it loads, with lexical overlap as the fallback, a score threshold, and a character budget.

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

ACTIVE. pypdf 6.16.2 (BSD, same pin as browser-use), python-docx, and openpyxl. Results carry page, paragraph, or sheet. PyMuPDF is not imported.

## SPECIALIST ENGINE STATUS

`run_coding` calls the sandbox and returns evidence for a supervisor. OpenHands, Aider, and SWE-agent are not installed. Status is not_applicable unless the task class is software engineering.

## OBSERVABILITY STATUS

Every work package writes rows to the `traces` table. `GET /work-packages/{id}/traces` returns them. Campus has a Traces drill-down on the status panel. `AYVEN_TRACE_PATH` still appends JSON lines. Langfuse is not required.

## SECURITY STATUS

System, objective, observation, and web are separate strings. Web text is fenced. Injection phrases are detected and do not replace the objective. Rebuff is not used. llm-guard is not installed.

## GARAK STATUS

OPTIONAL. `scripts/run_security_eval.sh` exits 0 unless `AYVEN_RUN_GARAK=1` and a local base URL is set. It is not in pytest.

## DEPENDENCY HEALTH

`apps/api/app/intelligence/deps.py` and `apps/api/constraints.txt`. Core requirements now pin llguidance 1.3.0. Known risk: installing llama-cpp or browser-use without the constraints file can move `typing-extensions` or `click`. The banner prints versions and that warning. Preflight on a GPU aborts if llguidance is inactive or core imports are missing.

## RESULT PERSISTENCE STATUS

`validation/export_results.py`. A local tar is not VERIFIED. Git push of the small text bundle, including to a local bare repo, is read back before `verified` is true. `AYVEN_EXPORT_GIT_TOKEN` is used for an https remote and is not written into the status location. S3, rclone, and a Hugging Face dataset remain optional. Otherwise the harness prints `DO NOT STOP POD — RESULTS NOT EXPORTED`. No model weights are committed. A test against a local bare repo returns VERIFIED.

## CAMPUS STATUS

The served Campus (`apps/api/static/r3f/campus-app.jsx`) shows doing, why, stage, skills, stuck, needs you, finished, and trust from `GET /state` `campus_brief`. Traces stay in the intelligence endpoints. The React source in `apps/campus` has the same summary. A WebGL failure is caught so the status panel still renders. An approval on that panel is the same `POST /approvals/{id}/resolve` path. After approval the stage is `APPROVED` and "needs you" is no.

## PRODUCTION WORKFLOW STATUS

`POST /projects` still calls `orchestrator` → `workforce.run_objective` → `execution.run_objective`. The same function is what the harness binds. There is no second exam engine.

## TEST COUNT

85 passed, 0 failed. Command: `PYTHONPATH=. python3 -m pytest tests -q` from `apps/api` (with the repo root on `PYTHONPATH` so the export test can import `validation`). No GPU and no paid API.

## TEST RESULTS

Local pytest was run with `AYVEN_LLM_STUB=1`, `AYVEN_ALLOW_ESCALATION=0`, and fixture research. Result: **85 passed**. Frozen exam files were not edited. `scripts/check_dependency_lock.py` was already green on the pin check. The archive replay tests are in `tests/test_gpu_forensics.py`.

## UNSEEN EVAL RESULTS

All 13 categories in `benchmarks/unseen/tasks.json` run through `execution.run_objective`. On fixture mode with stub models, novel objectives opened no web pages, so research precision is null except document analysis (1.0, one attached spreadsheet). Repair rate on those runs is null because nothing was rejected. Supervisor catch rate is 0.0 where claims were challenged and none were disproved, and false rejection rate is 0.0. Calculation recorded retries 1. A separate test shows repair rate 1.0 when a rejected claim is supported by a fixture page. A page that says “ignore previous instructions” and tries to authorise a purchase is stored with `action_from_web: false` and does not create a send or purchase tool call. This does not measure Qwen.

## KNOWN LIMITATIONS

- Mem0, LiteLLM-as-gateway, DSPy, Langfuse, Garak, and llm-guard are not on the request path. Crawl4AI's install pulls LiteLLM in as a library; `gateway.py` does not call it.
- Repair research is one bounded round for the rejected claim. It does not restart the whole programme.
- Crawl4AI extraction here is the markdown generator on HTML already fetched or on a local file. It does not launch a browser crawl.
- fastembed's pin wants `pillow<12` while browser-use wants `pillow==12.3.0`. The constraint file keeps pillow 12.3.0 and click 8.3.3. Reapply those pins after the memory or crawl extra.
- Workflow transitions that the table forbids are stored as forced rather than crashing a package. That is visible in the verification row.
- Relevance filtering is strict for model-originated queries only. An operator-supplied query can still open a weak page and label it irrelevant.
- Entity extraction is capitalisation and keywords. It will miss lowercase names and will split awkwardly on titles.
- The byte-level llguidance tokenizer proves the grammar. A live llama.cpp process must be given `llguidance_grammar` by the server for the mask to apply during sampling. The post-check still refuses a bad string.
- Campus 3D still loads React from a CDN. The new panel is in the page source.

## WHAT REMAINS UNPROVEN WITHOUT REAL MODELS

Whether Qwen 8B/32B/30B will satisfy the frozen exams, whether research precision rises on live pages, whether the repair rate on the next GPU run is above zero, and whether the manager’s own rationale is useful. The control plane can reject garbage. It cannot supply the missing judgement.

Label: **SYSTEM VERIFIED** for the local tests. **MODEL INTELLIGENCE UNVERIFIED**.

## FRANKENSTEIN READINESS

This build is not READY. Local tests executed the control plane. Qwen exam quality is ACTIVE BUT NOT MODEL-VALIDATED. A GPU run has not been executed for 2.0.0.

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
