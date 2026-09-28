# v2 finishing audit

Version 2.0.1. `GPU_VALIDATED` is false. This pass did not restart the architecture and did not rerun the frozen exams on a GPU. Every row was checked against the code path used by `scripts/run_ayven_validation.sh` and `POST /projects` → `orchestrator` → `workforce.run_objective` → `intelligence.execution.run_objective`. The harness binds the same `Programme`.

Local suite after this pass: **103 passed**. Command: `PYTHONPATH=/workspace:/workspace/apps/api python3 -m pytest apps/api/tests -q`.

`benchmarks/exams` and `render.yaml` were not edited. No GPU was started. `AYVEN_ALLOW_ESCALATION` stayed 0. No paid API was called.

The v1.1.1 forensic reconstruction in `docs/V2_REAL_GPU_FORENSICS.md` was not rewritten. The archive replay tests in `apps/api/tests/test_gpu_forensics.py` still pass against the same mechanisms (403 is a gap, `<think>` and planning prose are not queries, the bad football source is not primary, a fabricated club URL is a rejected route that asks for replacement research, a user-supplied price that is in the input stays, vending can be PARTIAL, manager escalations create approval rows).

## Component rows

| Component | File(s) | Production-path call site | Test(s) | Evidence | Verdict |
| --- | --- | --- | --- | --- | --- |
| Typed / format validation of tool requests | `boundary.py`, `constrained.py`, `models.py` | `complete_role` → `_openai_compat` sends `response_format`, `guided_json`, and the llguidance grammar. `parse_model` remains the backstop. | `test_finish.py::test_openai_compat_posts_guided_json`, `test_constrained_request_carries_the_schema_and_the_first_token_mask` | A local HTTP server recorded `guided_json` and strict `json_schema`. Post-check accepted `ResearchPlan`. | FIXED |
| Live token mask on the Qwen runtime | `constrained.py`, `validation/harness.py`, `scripts/gpu_employee.sh`, `scripts/gpu_manager.sh` | Harness `HfSession.generate(schema=...)` installs a first-token `{` logits processor. `GgufSession` passes `response_format`. vLLM scripts pass `--guided-decoding-backend xgrammar`. Supervisor llama-server takes the grammar per request. | CPU contract in `test_finish.py`. Probe: `constrained.write_probe` from `run_ayven_validation.sh` before model download. | The request shape and the brace mask are proven on CPU. No GPU server has answered. Status stays `MODEL_UNVALIDATED`. | FIXED (wiring) / not model-validated |
| `<think>` / reasoning isolation before tool execution | `boundary.py`, `models.py` | `_openai_compat` drops `reasoning_content` via `separate_channels` before `enforce_output`. Query planning uses `queries_from_plan_text`, which refuses reasoning. | `test_gpu_forensics.py`, `test_intelligence.py::test_think_tags_do_not_survive` | Archive think queries still cannot execute. | KEEP |
| Research-query contamination filter | `boundary.py`, `research.py` | `plan_queries` does not fall back to raw chain-of-thought. `reject_reasoning_query` runs before a search. | `test_gpu_forensics.py::test_archive_think_queries_and_media_cannot_execute` | Planning paragraphs and `<think>` queries stay out of the tool. | KEEP |
| Failed fetch → GAP | `audit.py`, `research.py` | `SOURCE_FAILURE` challenges stand as gaps, not contradictions, on the supervisor pass inside `Programme`. | `test_gpu_forensics.py::test_source_failure_is_a_gap_not_a_contradiction` | 403-style failures remain gaps. | KEEP |
| Targeted repair loop | `repair.py`, `execution.py` | `apply_repairs` runs inside the supervisor bind, cap `AYVEN_MAX_REPAIRS` (default 3). | `test_v2.py` repair tests, `test_finish.py::test_repair_classes_beyond_the_archive` | Stale date, arithmetic, entity mismatch, unit mismatch, partial source, and conflicting sources classify and stop at the cap. | FIXED |
| Claim ledger | `claims.py`, `db.py` | `add_claim` from `Programme._claims_for` and the document attach. | Existing ledger tests plus document provenance test | Rows still carry status, evidence, and authority. Locator and file hash are additive columns. | KEEP |
| Employee self-check | `selfcheck.py`, `execution.py` | Called from the employee bind before the supervisor. | `test_v2.py::test_production_path_records_contract_and_self_check` | Still on `run_objective`. | KEEP |
| Independent supervisor | `supervisor_check.py`, `execution.py` | Supervisor bind recomputes; it does not take the employee prose as the verdict. | `test_frankenstein.py` supervisor tests | Unchanged and still passing. | KEEP |
| Manager safety override | `resolution.py` | `apply_manager_veto` after the manager text is parsed. | `test_frankenstein.py::test_manager_proposal_is_followed_when_safety_allows_it` | A sound accept is not outvoted; a safety veto still escalates. | KEEP |
| Approval records and transitions | `execution.py`, `workflow.py`, `main.py` | Escalation and clarify create a row and move `ESCALATED` → `AWAITING_APPROVAL`. `POST /approvals/{id}/resolve` and `GET /approvals`. | `test_v2.py::test_approval_record_exists_for_clarify_and_for_escalation`, `test_finish.py::test_campus_endpoints_list_approvals_and_evaluation` | List, reject, and project status respond on the API. | FIXED |
| Semantic memory | `memory.py` | `Programme._employee_user` calls `memory.retrieve` on every employee prompt. | `test_v2.py::test_semantic_memory_respects_threshold_and_budget`, `test_finish.py::test_retrieval_quality_thresholds` | bge-small paraphrase, near-duplicate, and distractor top-1 all 1.0 locally. Lexical path is the CI fallback if the embedder cannot load. Threshold 0.55 is unchanged. | FIXED |
| Run tracing | `observability.py`, `store.py`, `toolkit.py` | `bind_trace` around prepare and bind. Model calls, tool calls, sources, claims, and traces store the same `trace_id`. | `test_finish.py::test_trace_links_model_tool_and_claim` | Calculation run links the manager/model row and the calculator claim to one id. | FIXED |
| Prompt-injection guard | `security.py`, `research.py`, `execution.py` | Fetched pages set `injection_signals` and `action_from_web=False`. Document lines that match the injection pattern are dropped from the published briefing. | `test_unseen.py::test_webpage_injection_does_not_trigger_an_action`, `test_finish.py::test_document_injection_does_not_become_an_action` | "send the secret" does not become a tool or a finding. | FIXED |
| PDF / DOCX / XLSX provenance | `documents.py`, `execution.py` | `_attached_document` when `AYVEN_DOCUMENT_PATH` is set and the objective names a document. A `DOCUMENT` claim is written on the parent. | `test_v2.py::test_pdf_docx_and_xlsx_keep_provenance`, `test_finish.py::test_document_provenance_reaches_the_ledger` | sha256 and `Stock!A1` are on the claim and in the parent findings. PyMuPDF is not imported. | FIXED |
| Crawl4AI | `crawl_adapter.py`, `research.py` | `_open_hit` calls `route_fetch`. Static HTML uses `DefaultMarkdownGenerator`. A JavaScript wall uses `crawl_live` (`AsyncWebCrawler`) when a browser can start, else Browser Use. | `test_v2.py::test_crawl4ai_extracts_a_local_page`, `test_finish.py::test_crawl_live_reads_a_local_javascript_page_or_stays_unverified`, `test_crawl_route_is_honest_about_markdown_versus_live` | Local Chrome executed a page whose marker existed only after script. Markdown extraction is not labelled a live crawl. | FIXED |
| Local Browser Use | `browser_adapter.py` | `fallbacks.on_http_result` launches it only for a JS wall when the live crawler is not used. | `test_frankenstein.py::test_browser_use_reads_a_local_http_page` | Read-only local HTTP page. CI installs Playwright Chromium so this is not import-only. | KEEP |
| MCP registration | `mcp_boundary.py`, harness env | Harness sets `AYVEN_MCP_SERVERS` to the local stdio server before `Programme`. | Existing frankenstein MCP test | Not reclassified. Still behind the Ayven boundary. | KEEP |
| Code sandbox | `code_sandbox.py` | `run_code` uses `unshare --net` or bubblewrap. rlimit alone does not execute. | `test_finish.py::test_sandbox_has_no_network` | A connect to `1.1.1.1:443` did not print `open`. | KEEP |
| Local model router | `registry.py` | `route_for` on every plan and model-call record. | Planner tests inside the production runs | Unchanged. | KEEP |
| Pydantic AI through the Ayven gate | `runtime.py` | Optional runtime. Default employee is still the native loop. | `test_v2.py::test_pydantic_ai_runtime_is_gated` | FunctionModel path still strips think-tool calls. Not the default employee. | KEEP |
| Result export | `validation/export_results.py`, `scripts/run_ayven_validation.sh` | Harness calls `verify_export` after the report. The shell calls `preflight_export` before `prepare.py`. | `test_v2.py::test_git_export_readback_is_verified`, `test_finish.py` dir and HTTP tests | Directory and HTTP tarballs match sha256 on read-back. Git-only still verifies. STOP POD only when `verified` is true. | FIXED |
| 13 unseen categories | `benchmarks/unseen/tasks.json`, `execution.py`, `render.py`, `completion.py` | Each objective goes through `run_objective`. | `test_unseen.py::test_each_unseen_category_runs_on_the_production_engine` | Safety (`nothing was sent`) and completion are separate. Calculation must show `42.00` and completion PASS. A gap must be explicit. | FIXED |
| GPU container / constrained probe | `scripts/gpu_*.sh`, `constrained.py` | Not started here. The probe file is written before download and records `MODEL_UNVALIDATED`. | CPU contract test | Flags are in the scripts. No weights were loaded. | FIXED (contract) / not model-validated |
| Dependency lock | `constraints.txt`, `scripts/check_dependency_lock.py`, `licences.py`, `.github/workflows/ci.yml` | CI installs core, Frankenstein, documents, and pydantic-ai under constraints. Crawl is installed under the same pins. The memory extra is installed separately because fastembed declares `pillow<12`, then click 8.3.3 and pillow 12.3.0 are reapplied. | `test_v2.py::test_dependency_pins_are_present`, `test_finish.py::test_licence_ledger_rejects_agpl_and_records_pins` | llguidance 1.3.0 MIT and pypdf 6.16.2 are recorded. PyMuPDF and Firecrawl stay REJECTED. | KEEP |
| Resume after crash | `recovery.py`, `execution.py` | `AYVEN_RESUME=1` returns the unfinished parent instead of inserting another. | `test_finish.py::test_resume_returns_the_same_package` | Same parent id, one parent row, a `resume` trace. | FIXED |
| Stage timeouts | `recovery.py` | Research inside `prepare_all` uses `run_bounded`. | `test_finish.py::test_stage_timeout_is_a_gap` | A 0.05s deadline returns `research_timeout`. Default deadline is 120s. | FIXED |
| Final evaluation | `recovery.py`, `main.py` | Stored on the package as `observability.evaluation`. `GET /projects/{id}/evaluation`. | Campus endpoint test | overall, task completion, safety, grounding, supervisor, manager, unsupported claims, and stats. | FIXED |
| Secrets in logs and archives | `observability.py`, `export_results.py` | `AYVEN_SECRET_CANARY` is stripped from traces and from the export bundle. | `test_canary_is_redacted_from_traces`, export dir test | The canary string is absent after redaction. | FIXED |

## Six-way classification

**ACTIVE AND EXECUTED** (on the production path, exercised by the local suite, not a stub): typed boundary and post-check, reasoning isolation, query filter, failed-fetch gap, repair loop, claim ledger, employee self-check, supervisor, manager veto, approvals and transitions, semantic memory with bge-small (lexical fallback if the model file is absent), traces with a shared id, injection guard on pages and documents, PDF/DOCX/XLSX with hash and locator, Crawl4AI markdown plus live `AsyncWebCrawler` when Chrome or Playwright Chromium is present, Browser Use, MCP registration, code sandbox with network off, local router, optional Pydantic AI gate, checksum export to a directory or HTTP target, git read-back as a secondary, 13 unseen categories with completion separate from safety, dependency pins and the licence ledger, SQLite resume, stage timeout, final evaluation API.

**ACTIVE BUT NOT MODEL-VALIDATED:** the live grammar mask on vLLM, llama.cpp, and transformers. The CPU contract and the first-call probe shape are in the tree. Qwen has not answered them. Exam quality on the three frozen tasks is still unverified. `GPU_VALIDATED` is false.

**OPTIONAL** (present, not on the default request path): DSPy offline helper, Langfuse, Garak, llm-guard.

**DEFERRED:** Graphiti.

**FAILED:** none in this local suite.

**REJECTED:** Mem0 as a hosted embedder, LiteLLM as the gateway, OpenHands, Aider, Letta, mini-SWE, SWE-agent, Firecrawl (AGPL), PyMuPDF (AGPL), Daytona (paid), Rebuff (archived).

## What only a GPU run can prove

- That vLLM with `--guided-decoding-backend xgrammar` accepts `guided_json` from the employee and manager servers and that the first token is actually `{`.
- That llama.cpp `create_chat_completion(response_format=...)` and llama-server honour the same schema for Qwen3-32B.
- That the transformers logits processor, with the real Qwen tokenizer, masks the first new token on `HfSession`.
- That the three frozen exams improve on the v1.1.1 failure modes under the real models. The local suite uses the stub and fixture pages.
- Wall-clock, VRAM, and token figures for that run.

Until that probe returns, the constrained report stays `MODEL_UNVALIDATED`.

## Pod commands

Set a durable target first. The script exits before `validation/prepare.py` if this probe cannot write and read back.

```bash
export AYVEN_EXPORT_DIR=/workspace/ayven-export
export AYVEN_ALLOW_ESCALATION=0
bash scripts/run_ayven_validation.sh --preflight-only
bash scripts/run_ayven_validation.sh
```

`STOP POD` is printed only when `validation/.export_status` contains `"verified": true`. Otherwise the script prints `DO NOT STOP POD — RESULTS NOT EXPORTED`.

Optional HTTP target instead of a directory: `AYVEN_EXPORT_HTTP_URL` (POST `/upload` with `X-Ayven-Name`, GET `/files/{name}`). Git, if still wanted, is `AYVEN_EXPORT_GIT_REMOTE` and `AYVEN_EXPORT_GIT_BRANCH`. It is not required for STOP POD when the directory or HTTP read-back succeeded.

Alternate OpenAI-compatible servers, still not started from this environment:

```bash
bash scripts/gpu_employee.sh    # vLLM Qwen3-8B, guided-decoding-backend xgrammar
bash scripts/gpu_manager.sh     # vLLM Qwen3-30B-A3B-AWQ, same flag
bash scripts/gpu_supervisor.sh  # llama-server Qwen3-32B GGUF; grammar is per request
```

Point the API at one of them with `AYVEN_LOCAL_LLM_BASE_URL`. The client sends `response_format` and `guided_json`. The harness in-process path does not need those servers; its first planned call passes `ResearchPlan` into `HfSession.generate`.
