# Decisions

## ADR-001 — Custom orchestrator, not CrewAI/LangGraph in-process
Own the task graph so campus/events stay stable.

## ADR-002 — SQLite for MVP
Zero ops. Schema is boring SQL.

## ADR-003 — OpenAI-compatible LLM adapter

## ADR-004 — DuckDuckGo HTML search as first tool

## ADR-005 — No OpenHands / no Sahni.ai code

## ADR-006 — FastAPI-hosted campus page

## ADR-007 — v0.2 campus renderer is R3F

## ADR-008 — Intelligence engine stays inside Ayven

v1.0.0 adds planning, skills, tools, a claim ledger, critic, verifier, and independent audit without replacing the orchestrator with LangGraph, CrewAI, Qwen-Agent, or OpenHands. External projects are adapters or patterns. Frontier calls require `AYVEN_ALLOW_ESCALATION=1`. Evidence and the calculator set factual boundaries. Model synthesis can be published after a grounding check removes unsupported facts.

## ADR-009 — Integrate capabilities, not hosts

v1.1.1 runs Qwen-Agent, Browser Use, and the MCP SDK inside Ayven. They do not become the orchestrator. A library is installed only when it adds a capability Ayven does not already have at the same quality. Overlapping hosts (OpenHands, Aider, SWE-agent, LangGraph, Microsoft Agent Framework, LiteLLM, Letta, Mem0) stay out. Code execution prefers `unshare` user/net/pid, then bubblewrap. If neither is available the weak level is reported and does not run code, and it does not abort a GPU validation. Docker is not required.

## ADR-010 — Intelligence proposes, control executes

v2.0.0 keeps Ayven as the operating system. A model may plan and propose. A tool runs only after the executable channel parses to a validated schema. Reasoning text is not a query, URL, claim, memory write, or customer sentence. llguidance constrains local JSON decoding when the server honours the grammar; the same grammar refuses a bad string before the handler. Pydantic AI, Mem0, LiteLLM, Crawl4AI, DSPy, Langfuse, and Garak were evaluated. Only the pieces that run inside Ayven's permissions are on the request path. The rest stay optional or rejected. See `docs/INTELLIGENCE_COMPONENTS.md`.
