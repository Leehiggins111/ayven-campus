# Open-source component audit

Updated 2026-09-25 for v1.1.0. Nothing was vendored. AGPL and source-available trees were not copied.

Core install: `apps/api/requirements.txt` (FastAPI, Uvicorn, HTTPX, Pydantic, pytest).
Frankenstein install: `apps/api/requirements-frankenstein.txt`. The GPU script installs both.

## Installed dependencies

| Project | Version | Licence | Why | What Ayven uses | Required? | Where | Replacement boundary |
| --- | --- | --- | --- | --- | --- | --- | --- |
| FastAPI | 0.115.6 | MIT | Campus API | Routes, health, work packages | Required | local | The HTTP app |
| Uvicorn | 0.34.0 | BSD | ASGI server | `uvicorn app.main:app` | Required | local | Any ASGI server |
| HTTPX | 0.28.1 | BSD | HTTP client | Search, fetch, local model calls | Required | remote HTTP | Another client behind `tools.py` |
| Pydantic | 2.13.5 | MIT | Models | Request bodies. Raised from 2.10.4 because MCP requires >=2.12 | Required | local | — |
| pytest | 8.3.4 | MIT | Tests | Local suite | Required for tests | local | — |
| Starlette | 0.41.3 | BSD | FastAPI base | Kept below 0.42 so FastAPI 0.115.6 still constructs | Required | local | Do not let MCP's newer SSE extra upgrade it |
| sse-starlette | 3.0.2 | BSD | MCP extra | Pinned so it does not demand Starlette >=0.49 | Required by MCP | local | Stdio does not use SSE |
| QwenLM/Qwen-Agent | 0.0.34 | Apache-2.0 (upstream; PyPI licence field empty) | Function-calling loop | `FnCallAgent` inside the employee boundary | Optional extra, on for validation | local | `AYVEN_AGENT_RUNTIME=native` |
| modelcontextprotocol/python-sdk | 2.1.1 | MIT | MCP client | stdio connect, list, call. browser-use 0.13.10 requires this exact MCP version | Optional extra | local | Empty `AYVEN_MCP_SERVERS` |
| browser-use/browser-use | 0.13.10 | MIT (upstream; PyPI licence field empty) | JS and navigation | Read-only `BrowserSession` | Optional extra | local Chrome | HTTP fetch when the page is static |
| python-soundfile | 0.14.0 | BSD-3-Clause | Import side effect | qwen-agent imports it at startup. Ayven does not transcribe audio | Optional extra | local | Remove if a future qwen-agent stops importing it |
| openai | 2.26.0 (transitive) | Apache-2.0 | Qwen-Agent's OpenAI-compatible client | Used only when `AYVEN_LOCAL_LLM_BASE_URL` is set. No paid call is made by installing it | Transitive | local process, remote only if that URL is set | The scripted model in tests |

Qwen weights are not installed in this checkout. The harness downloads them on a GPU pod. Their licence is Apache-2.0.

## Studied and not installed

| Project | Licence | Decision | Why not |
| --- | --- | --- | --- |
| huggingface/smolagents | Apache-2.0 | STUDIED | A second code-agent loop. The sandbox is `unshare`, and Qwen-Agent already runs tools. |
| lukeswade/deep-research | MIT | STUDIED | The plan/read/review/gap pattern is in `research.py`. The project had no maintenance to justify a dependency. |
| BerriAI/litellm | MIT for non-enterprise code; `enterprise/` is separate | REJECTED | Ayven already calls an OpenAI-compatible local server. The package is large and the licence is split. |
| letta-ai/letta | Apache-2.0 | REJECTED | Needs its own server and a model to write memory. No local embedder without a GPU or a paid API. |
| mem0ai/mem0 | Apache-2.0 | REJECTED | The gain is vector recall. Same missing embedder. SQLite overlap ranking is enough for the include/exclude test. |
| langchain-ai/langgraph | MIT | REJECTED | Would replace the work-package graph. |
| microsoft/agent-framework | MIT | REJECTED | Would replace the employee, supervisor, and manager loop. |
| All-Hands-AI/OpenHands | MIT | REJECTED | Host agent with its own shell and browser. It would skip permissions and the ledger, and it implies a software department. |
| Aider-AI/aider | Apache-2.0 | REJECTED | Owns the edit loop. The frozen exams do not edit a repository. |
| SWE-agent/SWE-agent | MIT | REJECTED | Host agent, Docker-centric. Docker is not on this VM. |
| SWE-agent/mini-SWE-agent | MIT | REJECTED | Smaller, but still its own shell loop. The sandbox keeps that control in Ayven. |
| firecrawl/firecrawl | AGPL-3.0 | REJECTED | Not vendored. Adapter stays disabled. No request is sent. |
| anthropics/skills | Mixed; document skills are source-available | REJECTED | No skill text copied. The local loader follows the Apache-2.0 agentskills folder layout. |

## Replacement boundaries

- Skills: another directory of `SKILL.md` files via `AYVEN_SKILLS_DIR`.
- Agent loop: `AYVEN_AGENT_RUNTIME=native` skips Qwen-Agent.
- Browser: `browser_adapter.py`. HTTP fetch does not import it when the page opens.
- MCP: `AYVEN_MCP_SERVERS` JSON. The client does not hard-code a server.
- Sandbox: `code_sandbox.py`. Arithmetic stays on the calculator.
- Memory: `memory.py`. A future local embedder would sit behind `retrieve()`.
- Models: `AYVEN_EMPLOYEE_MODEL`, `AYVEN_SUPERVISOR_MODEL`, `AYVEN_MANAGER_MODEL`, `AYVEN_CODING_MODEL`.
