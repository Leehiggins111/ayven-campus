# Frankenstein readiness

Version 1.1.1. This is the list of what the next GPU run actually exercises.

Label for everything below that was executed here: **INTEGRATION TESTED. MODEL INTELLIGENCE UNTESTED.** No GPU was started. No paid API was enabled.

| Capability | State | What actually runs |
| --- | --- | --- |
| Qwen-Agent employee runtime | ACTIVE | `FnCallAgent` from qwen-agent 0.0.34. Tools go through `authorize()` before any effect. A loaded harness session or `AYVEN_LOCAL_LLM_BASE_URL` drives a live multi-turn loop. Stub and one-shot completions are replay and are labelled replay. A crash falls back to the native text path and records the error. |
| Skills | ACTIVE | Classify, discover metadata, load only the matching `SKILL.md`, put the tool/evidence/check/permission contract in the employee system prompt, record the names. |
| Iterative research | ACTIVE | The employee model plans the first queries from the objective and the skill text when a real model is loaded. Stub and no-model runs derive queries from the objective text only. Review runs after each round and may add a query or a gap. A reviewer exception is stored as a failure. Budgets: rounds, searches, pages, browser actions, time. |
| Browser Use | ACTIVE | browser-use 0.13.10 plus system Chrome. Read and navigate only. HTTP fetch remains the default. A JS wall escalates when a browser function is available. Fixture hosts do not launch Chrome. |
| MCP | ACTIVE | mcp 2.1.1 stdio client. Servers come from `AYVEN_MCP_SERVERS`. The validation harness points that config at `app.intelligence.mcp_local_server`. Read vs action is classified. Actions need a capability and approval. A dead server marks the call unavailable. |
| Claim ledger | ACTIVE | Unchanged from 1.0.1. Statuses, entailment, supersession, empty evidence cannot stay supported. |
| Grounding | ACTIVE | Unsupported prices, URLs, and contacts are stripped before publication. |
| Critic | ACTIVE | Deterministic critique of the draft against the ledger. |
| Verifier | ACTIVE | Deterministic checks, including the door totals. |
| Supervisor tools | ACTIVE | Calculator when the package is a quote. Material claims also get an independent search written from the claim, then a fetch, and a browser only when that HTTP fetch fails and browsing is allowed. Budget: 2 searches and 3 fetches per package. The ledger says independently confirmed, contradicted, re-read, or budget exhausted. The prompt still has no employee reasoning. |
| Manager model judgement | ACTIVE | The manager text is parsed into SYNTHESISE / RESEARCH_MORE / RETURN / CLARIFY / ESCALATE. That proposal is the decision when it is as cautious as the safety result or more so. A more permissive proposal is veto-forced. The harness prints model-proposed or veto-forced. |
| Memory | ACTIVE | SQLite rows with scope, provenance, expiry, and supersession. Retrieval ranks by token overlap and drops score 0. Only those rows are injected, and they are labelled as not evidence. |
| Model routing | ACTIVE | General employee, stronger supervisor, coding-capable id (defaults to the employee model), calculator, browser, and code sandbox. The reason is stored. Role and model id stay separate. Frontier stays off. |
| Code sandbox | REPORTED, not a hard gate | Preferred isolation is `unshare-user-net-pid`, then `bubblewrap-unshare-net`. If neither works, the level is `rlimit-subprocess-no-network-unverified` and code execution stays disabled. `AYVEN_ALLOW_CODE` defaults to 0. The three exams use the calculator. A missing sandbox does not abort the GPU run. |
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

`./scripts/run_ayven_validation.sh` runs capability preflight before `validation/prepare.py`, so a missing hard gate stops the run before a model download or a llama.cpp CUDA build. `--preflight-only` installs dependencies, prints the preflight, and exits.

Hard gates: Qwen-Agent, browser, MCP, skills, research loop, claim ledger, critic, verifier, supervisor tools, manager judgement, memory, routing, and the capability registry. The code sandbox is printed with the isolation level that was actually achieved. `REPORTED` does not abort the run. sudo is used only when the user is not root and sudo exists. If the system Chrome package fails, the script tries Playwright Chromium and sets `AYVEN_CHROME_PATH`. Install errors are printed. They are not hidden.

A dry run on a machine without a GPU still executes the exams and cannot be an overall PASS.

## Isolation that is actually in effect

On this VM the achieved level is `unshare-user-net-pid`. `print(1+1)` returned `2`, and an HTTP GET from inside the sandbox failed. `unshare -n` without the user namespace is not permitted here. There is no Docker daemon and no bubblewrap. A host where unshare and bubblewrap both fail reports `rlimit-subprocess-no-network-unverified` and does not execute model code.
