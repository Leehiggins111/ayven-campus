# Intelligence architecture

Version 1.1.0. Status: **FRANKENSTEIN RUNTIMES. LOCAL INTEGRATION TESTS PASS. NOT YET VERIFIED ON GPU.**

Ayven is the product. Qwen-Agent, MCP, Browser Use, and the other projects in `INTELLIGENCE_COMPONENTS.md` are optional components behind Ayven interfaces. The campus, work packages, approvals, and SQLite state are unchanged in role.

## What is real in this build

| Piece | State |
| --- | --- |
| Work-package loop, plans, skills, tools, claim ledger, critic, verifier, supervisor audit, manager decision | REAL, deterministic |
| Door arithmetic | REAL, two implementations (expression parser and integer pence) |
| Web research in `AYVEN_RESEARCH_MODE=live` | REAL HTTP search and fetch |
| Web research in fixture mode (default while `AYVEN_LLM_STUB=1`) | SIMULATED. Frozen excerpts of public pages, labelled `FIXTURE_SNAPSHOT` |
| Employee / supervisor / manager prose | STUBBED unless a local OpenAI-compatible server or the GPU harness is running |
| Frontier APIs | OFF. `AYVEN_ALLOW_ESCALATION=0` |
| Qwen-Agent employee tool loop | REAL import and `FnCallAgent`. Local tests use a scripted model. GPU uses the same loop around the loaded model. |
| Browser Use | REAL read-only HTTP navigation when Chrome is present. Not used when plain HTTP succeeds. |
| MCP client | REAL stdio session when `AYVEN_MCP_SERVERS` is set. |
| Code sandbox | REAL `unshare` user/net/pid namespace. Off unless `AYVEN_ALLOW_CODE=1` and approved. Not Docker. |
| Firecrawl | NOT INSTALLED. AGPL, not vendored. |
| Qwen quality on the three exams | NOT YET VERIFIED ON GPU |

## Lifecycle

A parent work package is the job. Children are focused pieces of that job, not three copies of the same chat.

1. **Work package.** Milo still opens the project. The manager opens the parent package.
2. **Task classification.** Deterministic: internal-door quote, football tickets, vending prospects, calculation, business research, web research, or trivial.
3. **Planning.** A plan is stored: objective, deliverable, factual and current-data needs, tools, skills, calculations, evidence bar, risk, approval, output shape, stages, and budget.
4. **Skill selection.** Only the matching skills are loaded. A door job does not receive the vending skill.
5. **Model selection.** Calculation goes to the calculator. Draft commentary uses the employee role. Audit uses the supervisor role. Orchestration uses the manager role. Escalation is a disabled route.
6. **Tool selection.** The tool list comes from the plan and from the agent's capabilities.
7. **Research or action.** One shared research pass for the programme. Queries are decomposed. Pages are opened. Failures are stored. There is no second and third identical search.
8. **Evidence collection.** Snippets are leads. Opened text is evidence. Source rank and freshness are stored.
9. **Claim ledger.** Extractive claims and calculator claims are inserted. Confidence comes from rank, freshness, and whether the page was opened.
10. **Draft.** The published text is rendered from the ledger. Model prose is commentary and is not allowed to add prices, URLs, or companies.
11. **Critic.** Lists assumptions, gaps, weak claims, and drift.
12. **Verifier.** Recomputes door totals in integer pence and checks that material claims have evidence.
13. **Revision.** A failed calculation package is rendered again from the same inputs. The budget is `AYVEN_MAX_ATTEMPTS` (default 2). No infinite loop.
14. **Supervisor audit.** The supervisor sees the objective, plan, draft, claims, and gaps. The decision is ACCEPT, RETURN, TAKE_OVER, or ESCALATE. A model suggestion is recorded and does not outvote the checks. TAKE_OVER rewrites from the ledger. RETURN is used when a retry could still help.
15. **Manager.** Sees every audit. Decides SYNTHESISE, CLARIFY, or ESCALATE. CLARIFY creates an approval. ESCALATE records `escalation_required` and does not call a frontier model.
16. **Approval and results.** Distribution still routes the parent. Contact, purchase, and customer quotes wait for Lee.

Trivial tasks skip research, browsing, and calculation.

## Budgets

- `AYVEN_MAX_RESEARCH_ROUNDS` default 2. Every planned query still runs; extra rounds are only for gaps.
- `AYVEN_MAX_ATTEMPTS` default 2.
- `AYVEN_MAX_MANAGER_LOOPS` default 1.

## Where it lives

`apps/api/app/intelligence/` is the engine. `skills/` is the skill catalogue. `benchmarks/exams/` is the frozen exam text. Campus rendering is untouched.

## Memory, permissions, quality

Memory is SQLite, not a transcript. Scopes are WORK_PACKAGE, PROJECT, CUSTOMER, DOMAIN, and COMPANY. Rows carry provenance and can expire or be superseded. The door job stores the labour ambiguity and the manager decision, not the model chat.

Capabilities: READ_WEB, BROWSE_WEB, RUN_CALC, RUN_CODE, READ_FILES, WRITE_FILES, SEND_EMAIL, MODIFY_DATABASE, PURCHASE, EXTERNAL_CONTACT. Research employees have read-web and the calculator. They cannot email, buy, or contact anyone. High-impact tools require an approval even for a role that might gain them later.

The quality row is an operational signal: coverage, source quality, claim support, freshness, contradictions, unknowns, calculation checks, supervisor outcome, verification pass rate. It is not a model self-score and not an objective truth score. Fixture freshness is scored lower than a live fetch.

Observability on the parent package stores the plan, selected models, skills, queries, pages, failures, supervisor decisions, manager decision, token totals, and errors. Think tags are stripped before storage. Chain-of-thought is not kept.

## API

`GET /state` includes `intelligence` (plans, skills, tool calls, claims, verification, quality, model calls). `GET /work-packages/{id}/intelligence` is the full record for one package. `GET /health` reports version `1.1.0`, `validation_status`, and `gpu_validated: false`.
