# Changelog

## 1.1.1 — 2026-09-25

Repair after the v1.1.0 review. The frozen exams are unchanged.

- Research no longer lists exam domains or exam seed queries. Official sources are detected from the entity name, site self-identification, and public-sector suffixes. Stub runs build queries from the objective text. A review runs after each round, and a reviewer exception is a recorded failure.
- The supervisor searches from the claim itself, within two searches and three fetches, and records independent confirmation, contradiction, re-read, or an exhausted budget.
- The manager decision records whether it was model-proposed or veto-forced. A more cautious proposal stands.
- A loaded session or `AYVEN_LOCAL_LLM_BASE_URL` drives Qwen-Agent live. Replay of one completion is labelled replay.
- The GPU script does not require sudo, falls back to Playwright Chromium, runs preflight before model download, and accepts `--preflight-only`. A weak code sandbox is reported and does not abort the run.

## 1.1.0 — 2026-09-25

Frankenstein runtimes. Ayven still owns work packages, permissions, evidence, and approvals.

- Qwen-Agent 0.0.34 runs the employee tool loop inside that boundary. `AYVEN_AGENT_RUNTIME=qwen-agent|native|auto`. A failure falls back to the native runtime.
- Browser Use 0.13.10 reads HTTP pages. It does not click, submit, or buy. HTTP fetch stays the default. A JavaScript wall can escalate to the browser.
- The MCP Python SDK 2.1.1 connects to servers listed in `AYVEN_MCP_SERVERS`. Read and action tools are distinguished. Actions need approval.
- Selected `SKILL.md` files, including their tool and evidence contract, are what the employee runtime sees.
- Research reviews what it opened, may run one bounded next query, and can record "I still don't know" as a gap.
- Code runs in `unshare --user --map-root-user --net --pid`, not Docker. The network namespace is off. The filesystem is still the host. The calculator remains the arithmetic tool. `AYVEN_ALLOW_CODE` defaults off.
- Memory retrieval ranks SQLite rows and injects only overlaps. Letta and Mem0 are not installed.
- The manager model proposes SYNTHESISE, RESEARCH_MORE, RETURN, CLARIFY, or ESCALATE. Deterministic safety can veto. The stored rationale is not chain-of-thought.
- The supervisor can calculate or re-read evidence. It does not receive the employee's reasoning.
- Pydantic is 2.13.5 because the MCP SDK requires Pydantic 2.12 or newer. Starlette stays 0.41.3 so FastAPI 0.115.6 still loads.

## 1.0.1 — 2026-09-25

Pre-GPU hardening. The architecture is unchanged.

- Vending lists real opened-page candidates as FACT, INFERENCE, and UNKNOWN. An empty ledger is a safety pass and a task-completion fail.
- Model synthesis is allowed inside evidence boundaries. A grounding check removes unsupported facts before publication.
- The supervisor records a challenge for material claims. The manager records how a disagreement was resolved before SYNTHESISE, CLARIFY, or ESCALATE.
- Research retries a failed fetch, follows a bounded relevant link, and says when live retrieval failed. It does not fill gaps from model memory.
- The GPU harness prints one scorecard and writes the archive before any stop-pod line.

## 1.0.0 — 2026-09-25

Intelligence engine. Architecture and local tests are in. Real Qwen validation is not.

- Planner, selective skills, structured tools, evidence research, claim ledger, critic, verifier
- Supervisor audit that can accept, return, take over, or escalate without copying the employee
- Manager orchestration with approval and escalation disabled by default
- Deterministic door scenarios that keep labour per-door versus per-job unresolved
- Model registry. Calculator for arithmetic. Frontier calls stay off when `AYVEN_ALLOW_ESCALATION=0`
- SQLite migrations add plans, skills, tool calls, claims, verification, and quality without dropping v0.4 rows
- GPU harness runs the same loop and archives results before the pod is stopped

## 0.2.1 — 2026-09-24

- In-world canvas billboard labels
- Plaza ring, radial paths, perimeter trees

## 0.2.0 — 2026-09-24

- Multi-building HQ campus (R3F)
- Camera focus, inspector, command board, approval chrome

## 0.1.1 — 2026-09-24

- Campus served at GET / and /campus

## 0.1.0 — 2026-09-24

- Initial monorepo: API + campus + docs
- Football-trips MVP path
