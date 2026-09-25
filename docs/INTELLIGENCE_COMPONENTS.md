# Open-source component audit

Audited 2026-09-25 from the GitHub API (licence, stars, last push) and the repository licence files. Nothing in this list was vendored. Ayven's runtime dependencies are unchanged: FastAPI, Uvicorn, HTTPX, Pydantic, pytest.

## Dependency and licence ledger

| Component | Licence | Runtime dependency? |
| --- | --- | --- |
| FastAPI, Uvicorn, Pydantic, HTTPX, pytest | MIT (see `THIRD_PARTY_NOTICES.md`) | Yes, already installed |
| Qwen weights used by the harness | Apache-2.0 | Not installed here. Downloaded only on a GPU pod by the existing prepare script |
| Qwen-Agent, MCP SDK, Browser Use, smolagents, LiteLLM, Letta, Mem0, LangGraph, Agent Framework, OpenHands, Aider, SWE-agent, mini-SWE-agent, deep-research | See below | No |
| Firecrawl | AGPL-3.0 | No, and its source is not copied |
| anthropics/skills document skills | Source-available, not Apache, for docx/pdf/pptx/xlsx | Not copied |

## Candidates

| Project | Licence | Activity (pushed) | Stars | Local models | Usefulness | Strategy | Selected? |
| --- | --- | --- | --- | --- | --- | --- | --- |
| QwenLM/Qwen-Agent | Apache-2.0 | 2026-03-04 | 17128 | Yes. Tool calling, MCP, code interpreter, RAG | High for Qwen tool loops | Optional adapter. Patterns adopted: tool schema, multi-step tools, stop when evidence is enough. Not imported | Adapter only. Default off |
| agentskills/agentskills | Apache-2.0 | 2026-08-09 | 25691 | Format, not a model | Skill folder standard | Own loader. `SKILL.md`, scripts, references, assets. Progressive disclosure | Yes, adapted |
| anthropics/skills | Mixed. README: many Apache-2.0; document skills source-available. GitHub licence metadata empty | 2026-09-24 | 178270 | Examples for Claude | Format reference only | Studied. No skill text copied | No |
| modelcontextprotocol/python-sdk | MIT | 2026-09-25 | 24402 | Transport, not a model | Dynamic tools later | Boundary module. SDK not installed. No session opened | Boundary only |
| browser-use/browser-use | MIT | 2026-09-25 | 116287 | Has its own agent loop | JS-heavy pages | Adapter reports `not_installed`. Not a dependency | Optional, not selected for default |
| lukeswade/deep-research | MIT | 2026-08-23 | 0 | Local OpenAI-compatible | Research loop shape | Borrowed: decompose, open, verbatim evidence, gap-driven second search. Not installed (immature) | Pattern only |
| huggingface/smolagents | Apache-2.0 | 2026-09-23 | 29495 | Yes, code agent | Sandbox ideas | Borrowed the caution. Own AST calculator. No Docker, no E2B | Pattern only |
| BerriAI/litellm | MIT for non-enterprise code. `enterprise/` is separate. GitHub SPDX was NOASSERTION | 2026-09-25 | 59629 | Many providers | Gateway | Deferred. Ayven already speaks OpenAI-compatible locally. Large dependency | No |
| letta-ai/letta | Apache-2.0 | 2026-09-10 | 24884 | Yes | Stateful memory | Studied scopes and provenance. Own SQLite memory. No server | Pattern only |
| mem0ai/mem0 | Apache-2.0 | 2026-09-25 | 66010 | Yes | Memory layer | Studied. Rejected hosted memory. Own scoped rows | Pattern only |
| langchain-ai/langgraph | MIT | 2026-09-23 | 42288 | Via LangChain | Graph runtime | Would replace the orchestrator. Rejected | No |
| microsoft/agent-framework | MIT | 2026-09-25 | 13797 | Yes | Multi-agent workflows | Would replace Ayven's loop. Deferred | No |
| All-Hands-AI/OpenHands | MIT | 2026-09-25 | 89167 | Yes | Software-engineering agent | Wrong product shape, heavy. Rejected | No |
| Aider-AI/aider | Apache-2.0 | 2026-05-22 | 49186 | Yes | Coding pair tool | Deferred until a software department exists | No |
| SWE-agent/SWE-agent (also reachable as princeton-nlp/SWE-agent) | MIT | 2026-09-21 | 20407 | Yes | Issue fixing | Deferred | No |
| SWE-agent/mini-swe-agent | MIT | 2026-09-21 | 7971 | Yes | Small coding agent | Deferred. Simplicity noted, not adopted | No |
| firecrawl/firecrawl | AGPL-3.0 | 2026-09-25 | 184701 | API | Page extraction | Adapter is disabled. No source copied. No request sent | No |

## Replacement boundaries

- Skills can be replaced by another folder of `SKILL.md` files (`AYVEN_SKILLS_DIR`).
- Search and fetch can be replaced behind `research.py` without touching the ledger.
- Browser Use can be filled in later at `browser_adapter.py`.
- MCP servers can be listed in `AYVEN_MCP_SERVERS` once the SDK is installed. Action tools stay approval-gated.
- Qwen-Agent can be turned on with `AYVEN_USE_QWEN_AGENT=1` only after `qwen-agent` is installed. The hook returns control to Ayven's loop.
- Firecrawl cannot be vendored. A future HTTP client would be a separate process boundary, still off by default.
- The model registry can point employee, supervisor, and manager at other local ids. The calculator route stays deterministic.

## Rejected as the orchestrator

LangGraph, CrewAI, Microsoft Agent Framework, OpenHands, and Qwen-Agent as the host process. ADR-001 still holds: Ayven owns the task graph so packages, events, and approvals stay stable.
