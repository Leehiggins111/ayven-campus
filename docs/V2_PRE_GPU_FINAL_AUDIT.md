# Pre-GPU final audit (v2.0.2)

Baseline was v2.0.1 at `fe948e4`, draft PR #1, 103 tests green. This pass did not restart the architecture, did not rebuild the control plane, and did not rewrite `docs/V2_REAL_GPU_FORENSICS.md`. `GPU_VALIDATED` stays false. Local suite after this pass: **110 passed**.

The campus is the same 3D multi-building headquarters (`apps/api/static/r3f`). Buildings, roads, work crates, and Ask Milo are still there. The panels read `GET /campus/view`, which is assembled from the work-package ledger.

## Requirement classification

| Requirement | Classification | What this pass did |
| --- | --- | --- |
| 403-style fetch is a gap, not a contradiction | ALREADY SATISFIED — LEAVE IT ALONE | Re-read `audit.py` (`SOURCE_FAILURE` stands as a gap). Existing archive test still in the 110. |
| `<think>` and planning-paragraph queries never execute | ALREADY SATISFIED — LEAVE IT ALONE | `reject_reasoning_query` and `queries_from_plan_text` are unchanged. |
| Entity and source validation | ALREADY SATISFIED — LEAVE IT ALONE | `_entity_follow_up` still drives a bad-entity challenge. No exam names were added to engine Python. |
| Fabricated URL is disproved and replacement research is requested | ALREADY SATISFIED — LEAVE IT ALONE | Repair still treats an unopened URL as `REMOVE_CLAIM` / follow-up research. |
| User-supplied facts that are in the objective stay | ALREADY SATISFIED — LEAVE IT ALONE | `_corpus` still includes the objective before grounding. |
| Manager escalation writes an approval and `AWAITING_APPROVAL` | ALREADY SATISFIED — LEAVE IT ALONE | `resolve_approval` still lands on `APPROVED` for the existing direct call. Resume is a later step. |
| 3D multi-building campus | ALREADY SATISFIED — LEAVE IT ALONE | Not redesigned. Avatars no longer bob on a timer; colour comes from `visual_state`. |
| At-a-glance company picture (doing, who, why, stage, tools, skills, research, trust, supervisor rejections, repair, manager, needs-you, finished, time, cost) | INCOMPLETE — FINISH IT | Finished. `campus_view` feeds the glance. Cost is `unknown` unless `measured_cost_usd` is stored. The stub token estimate is not shown as a bill. |
| Work-package workflow strip | INCOMPLETE — FINISH IT | Finished. Order follows the production path: REQUEST, PLANNING, RESEARCH, EVIDENCE, EMPLOYEE, DRAFT, SUPERVISOR, REPAIR, MANAGER, APPROVAL or CLARIFICATION, COMPLETE. Research is before the employee draft because that is when it actually runs. |
| Agent visual state from the backend | INCOMPLETE — FINISH IT | Finished. `agents.visual_state` plus `agent.visual` events: IDLE, PLANNING, RESEARCHING, USING_TOOL, WRITING, REVIEWING, REPAIRING, WAITING, NEEDS_APPROVAL, COMPLETED, FAILED. Legacy `status` words are unchanged. |
| NEEDS YOU approval, real Approve / Reject, resume to completion | SATISFIED BUT NEEDS HARDENING | The old card only showed a summary and did not resume. Hardened: the banner shows what, why, if approved, if rejected, and evidence. `POST /approvals/{id}/resolve` with `approved` records `APPROVED` and then moves the same parent APPROVED → ACTIONING → COMPLETED. Reject lands on FAILED and `campus_stage` REJECTED. Nothing is sent. |
| NEEDS CLARIFICATION, same package continues | INCOMPLETE — FINISH IT | Finished. A line `NEED:`, `ASK LEE:`, or `CLARIFICATION:` pauses that parent in `AWAITING_CLARIFICATION` before research. `POST /work-packages/{id}/clarification` stores the answer and continues that id. |
| Repair visibility without chain-of-thought | INCOMPLETE — FINISH IT | Finished. The panel reads repair actions (claim, type, status, resolution). `strip_think` runs before display. |
| Evidence drill-down | INCOMPLETE — FINISH IT | Finished. Closed until opened. Claim, support, source, type, authority, locator, hash, supervisor decision. |
| Final result for a finished or rejected package | SATISFIED BUT NEEDS HARDENING | `GET /projects/{id}/evaluation` already existed. The campus now shows summary, deliverable, findings, gaps, confidence, manager, time, and cost. |
| API and browser tests, screenshots | INCOMPLETE — FINISH IT | `test_campus_os.py` and headless Playwright `test_campus_browser.py` against the real FastAPI app. Shots in `docs/campus-screenshots/`. |
| Real Qwen quality, grammar on a live GPU, frozen exam scores | NOT PRACTICAL PRE-GPU — EXPLAIN WHY | No GPU was started. The stub and fixture pages cannot prove model intelligence. `GPU_VALIDATED` stays false. |
| Paid frontier escalation | NOT PRACTICAL PRE-GPU — EXPLAIN WHY | `AYVEN_ALLOW_ESCALATION` stays 0. A paid call would not make the campus honest. |

## Six-way classification

**ACTIVE AND EXECUTED:** everything in that class in `docs/V2_FINISHING_AUDIT.md`, plus the campus view, visual agent state, approval resume on the HTTP resolve path, clarification pause and resume, repair and evidence panels, and the final-result panel.

**ACTIVE BUT NOT MODEL-VALIDATED:** live grammar on vLLM, llama.cpp, and transformers. Frozen exam quality. `GPU_VALIDATED` is false.

**OPTIONAL:** DSPy offline helper, Langfuse, Garak, llm-guard.

**DEFERRED:** Graphiti.

**FAILED:** none in the local suite (110 passed).

**REJECTED:** Mem0 as a hosted embedder, LiteLLM as the gateway, OpenHands, Aider, Letta, mini-SWE, SWE-agent, Firecrawl (AGPL), PyMuPDF (AGPL), Daytona (paid), Rebuff (archived).

## What only a GPU run can prove

Unchanged from the finishing audit: that the guided-decoding flags are honoured by the real Qwen servers, that the first token is `{`, and that the three frozen exams move. Wall-clock, VRAM, and a measured dollar cost are unknown until that run. The campus will show `unknown` for cost until `measured_cost_usd` is written.
