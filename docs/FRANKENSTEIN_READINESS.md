# Frankenstein readiness

Version 1.1.0. This is the list of what the next GPU run actually exercises.

Label for everything below that was executed here: **INTEGRATION TESTED. MODEL INTELLIGENCE UNTESTED.** No GPU was started. No paid API was enabled.

| Capability | State | What actually runs |
| --- | --- | --- |
| Qwen-Agent employee runtime | ACTIVE | `FnCallAgent` from qwen-agent 0.0.34. Tools go through `authorize()` before any effect. `AYVEN_AGENT_RUNTIME=auto` uses it when the import works. A crash falls back to the native text path and records the error. |
| Skills | ACTIVE | Classify, discover metadata, load only the matching `SKILL.md`, put the tool/evidence/check/permission contract in the employee system prompt, record the names. |
| Iterative research | ACTIVE | Seed queries still run so the frozen exams open the same pages. A review step can add one next query or an explicit "I still don't know" gap. Budgets: rounds, searches, pages, browser actions, time. |
| Browser Use | ACTIVE | browser-use 0.13.10 plus system Chrome. Read and navigate only. HTTP fetch remains the default. A JS wall escalates when a browser function is available. Fixture hosts do not launch Chrome. |
| MCP | ACTIVE | mcp 2.1.1 stdio client. Servers come from `AYVEN_MCP_SERVERS`. The validation harness points that config at `app.intelligence.mcp_local_server`. Read vs action is classified. Actions need a capability and approval. A dead server marks the call unavailable. |
| Claim ledger | ACTIVE | Unchanged from 1.0.1. Statuses, entailment, supersession, empty evidence cannot stay supported. |
| Grounding | ACTIVE | Unsupported prices, URLs, and contacts are stripped before publication. |
| Critic | ACTIVE | Deterministic critique of the draft against the ledger. |
| Verifier | ACTIVE | Deterministic checks, including the door totals. |
| Supervisor tools | ACTIVE | One independent calculator run when there is a quote, otherwise a re-read of the first stored page. The supervisor prompt has the objective, draft, claims, and gaps. It does not include employee model text. |
| Manager model judgement | ACTIVE | The manager text is parsed into SYNTHESISE / RESEARCH_MORE / RETURN / CLARIFY / ESCALATE plus a short rationale. `resolve_manager` can veto a more permissive proposal. Think tags are stripped before storage. |
| Memory | ACTIVE | SQLite rows with scope, provenance, expiry, and supersession. Retrieval ranks by token overlap and drops score 0. Only those rows are injected, and they are labelled as not evidence. |
| Model routing | ACTIVE | General employee, stronger supervisor, coding-capable id (defaults to the employee model), calculator, browser, and code sandbox. The reason is stored. Role and model id stay separate. Frontier stays off. |
| Code sandbox | ACTIVE | `unshare --user --map-root-user --net --pid --fork --mount-proc` plus a parent `timeout`. Network is off. Filesystem is the host, not a container. Docker and bubblewrap are not installed. `AYVEN_ALLOW_CODE` defaults to 0, and `RUN_CODE` still needs approval. The exams keep using the calculator. |
| Capability registry | ACTIVE | `capabilities.health()` checks the calculator, the Qwen-Agent import, Chrome, the MCP import (and a real connect when servers are configured), unshare, SQLite, and the skill catalogue. The planner keeps only healthy tools. |
| Fallback system | ACTIVE | HTTP success does not launch the browser. JS wall launches it only when permitted and available, otherwise a gap is recorded. Qwen-Agent failure uses native text. A down MCP server is unavailable. A disabled sandbox tells the caller to use the calculator. Model memory is not written into evidence. |
| Specialist coding engine | ACTIVE for the sandbox only | A `software_engineering` package would be handed to the sandbox and then the supervisor. No software department is created. |
| OpenHands, Aider, SWE-agent, mini-SWE-agent | REJECTED | They are host agents with their own shell loop. They would bypass packages, permissions, and the ledger. |
| Letta, Mem0 | REJECTED | They need a memory server or an embedding model. This build has neither without a GPU or a paid API. SQLite overlap retrieval covers the required include/exclude behaviour. |
| LiteLLM | REJECTED | Ayven already speaks OpenAI-compatible HTTP locally. LiteLLM is a large gateway and its enterprise tree is a separate licence. |
| LangGraph, Microsoft Agent Framework | REJECTED | They would replace Ayven's task graph. ADR-001 and ADR-009. |
| smolagents | STUDIED, not installed | The useful idea is local code execution. Ayven uses `unshare` instead of a second agent loop. |
| deep-research (lukeswade) | STUDIED, not installed | The pattern is plan, open, review, gap. The repository had no meaningful maintenance. The loop lives in `research.py`. |
| Firecrawl | REJECTED | AGPL-3.0. Not vendored. The adapter stays disabled. |
| anthropics/skills document skills | REJECTED | Source-available terms. No skill text was copied. The agentskills folder format is implemented locally. |
| GPU quality of Qwen 8B / 32B / 30B-A3B | UNTESTED | The harness will run those weights through this loop. This repository has not done that run. |

## GPU rule

`./scripts/run_ayven_validation.sh` prints ACTIVE or INACTIVE for Qwen-Agent, browser, MCP, skills, research loop, claim ledger, critic, verifier, supervisor tools, manager judgement, memory, and the code sandbox.

If a GPU is visible and any of those is INACTIVE, the script exits before model download and does not call the run Frankenstein. A dry run on a machine without a GPU still executes the exams and cannot be an overall PASS.

## Isolation that is actually in effect

`unshare-user-net-pid`. Confirmed on this VM: `print(1+1)` returned `2`, and an HTTP GET from inside the sandbox failed. `unshare -n` without the user namespace is not permitted here. There is no Docker daemon and no bubblewrap.
