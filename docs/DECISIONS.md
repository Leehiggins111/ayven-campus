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
