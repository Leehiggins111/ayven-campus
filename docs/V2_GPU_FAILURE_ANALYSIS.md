# GPU run #2 failure analysis

Version under analysis: 1.1.1, commit `ab5869ade7c8a99905591131f447ba1c063b640c`.

## Artifacts

Superseded by `docs/V2_REAL_GPU_FORENSICS.md`.

The preserved run is archive `ayven-validation-20260928T102746Z` (real L40, real Employee/Supervisor/Manager, no stubs). The scorecard totals match the brief: 21 model calls, 90 tool calls, 56 sources, 131 claims created and challenged, 24 rejected, 0 retries, 631.26s, 5147 tokens, about $0.35. The 24 is `CONTRADICTED` (18) plus `unsupported_removed` (6). It is not the count of DISPROVED challenges (15) and not the count of non-SUPPORTED claims (42). Per-claim reconstruction, including which rejections were wrong, is in the forensics document. Do not treat the “artifacts were absent” wording from earlier drafts as current.

The figures below are the scorecard, now confirmed from `metrics.json`:

| Exam | Completion | Grounding | Calc | Supervisor | Manager | Unsupported claims | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Trades | PASS | PASS | PASS | PASS | PASS | 8 | FAIL |
| Football | PASS | PASS | PASS | PASS | PASS | 11 | FAIL |
| Vending | PARTIAL | PASS | PASS | PASS | PASS | 5 | FAIL |

Totals confirmed from the archive: 21 model calls, 90 tool calls, 56 sources opened, 131 claims created/challenged, 24 rejected, 0 retries, 631.26s, 5147 tokens, all roles REAL, about $0.35 inference. OVERALL FAIL.

The per-claim table is in `docs/V2_REAL_GPU_FORENSICS.md`. It was read from `ayven.db`, the role markdown, and the benchmark cards. It is not inferred from the code alone.

## What the 1.1.1 code actually did

These are code-path causes. They explain how the reported totals can happen. They are not a claim that each of the 24 rows had the same cause.

### 1. Reasoning could become a tool argument — TOOL ROUTING / MODEL

`parse_tool_lines` scanned every line of the raw model string, including lines inside `<think>…</think>`, and only then stripped tags from the leftover prose. A `TOOL` line inside reasoning was already a call.

The live Qwen path (`_LiveChat._chat_with_functions`) fed that raw string to the same parser. `plan_queries` and `_next_query` accepted planner text after a regex strip. A missing closer, a `<|think|>` token, or a `reasoning_content` field that a server folds into `content` survives a late regex.

There was no schema for `SearchRequest`. The string `think` or `think.mp3` was a legal query and a legal URL path. That matches the reported junk resources. Class: **TOOL ROUTING**, with **MODEL** as the producer of the junk.

### 2. Regex stripping was the only gate — TOOL ROUTING

`strip_think` ran after generation, on the way into storage. It did not run before the tool-line scan, and it did not constrain decoding. Post-hoc deletion cannot stop a call that has already been parsed. Class: **TOOL ROUTING**.

### 3. Research spent budget on weak hits — RESEARCH

Search hits were ordered with a binary “looks official” key and then opened. There was no entity record, no relevance threshold, and no rejection of media or reasoning tokens. A football objective could therefore open irrelevant URLs while missing a better official route. Ranking could also treat a page as official because the model’s query tokens appeared in the body. Class: **RESEARCH**. Not every bad page is a hallucination; some are real pages fetched for a bad query.

### 4. Approvals were skipped when the manager escalated — STATE MACHINE

`resolve_manager` turns non-calculation `CONTRADICTED` claims into `ESCALATE`. `_bind_manager` returned on `ESCALATE` **before** the approval insert. Exam-shaped work still has `human_approval_required`, and the safety text often asks for a person, but the approval row was never written. The package stage became `escalation_required`, not `AWAITING_APPROVAL`. Class: **STATE MACHINE**. This matches “manager outcome required clarification or approval, and no approval record exists” when the stored decision was `ESCALATE` or the process returned early.

### 5. Vending could be safe and still PARTIAL — CLAIM CLASSIFICATION / RESEARCH

Completion is scored separately from safety. A briefing that refuses to invent demand can pass grounding and still be PARTIAL or FAIL on usefulness if too few evidenced prospects were opened. That is a research-coverage failure, not a licence to invent organisations. Class: **RESEARCH**.

### 6. Rejected claims did not retry — SUPERVISOR / STATE MACHINE

`programme.retries` incremented only when `authoritative_decision` returned `RETURN`. `challenge_material_claims` could mark a claim `CONTRADICTED` (unsupported figure, unopened URL, footfall) while the rendered briefing still looked acceptable, so the supervisor score stayed PASS and retries stayed 0. Grounding removals were counted as unsupported and were not repaired. Class: **SUPERVISOR** for the missing repair, **CLAIM CLASSIFICATION** because “not in the evidence” was stored as contradiction, which then pushed the manager to `ESCALATE` instead of a local fix.

## Correct rejections vs engine bugs

Unsupported specifics (a URL or organisation that no opened page states) should be rejected. The archive shows that was correct for the four invented club URLs and the invented vending link, and incorrect for `Door: £82` (it is in the brief) and for the 15 dictionary failure rows (a 403 is a gap, not a contradicted fact). The engine bug after a real rejection was no repair, no retry, and an escalation that wrote no approval. Tool-routing junk (`think`, `think.mp3`) is a control bug even when the page exists. The row-by-row verdict is in `docs/V2_REAL_GPU_FORENSICS.md`.

## Root-cause classes used above

| Class | Where it shows up in 1.1.1 |
| --- | --- |
| MODEL | Qwen can emit reasoning and weak queries. The model is not the component that must execute them. |
| TOOL ROUTING | Tool lines and search strings were taken from raw text. |
| RESEARCH | No entity plan, no noise filter, no budget stop on irrelevant hits. |
| EXTRACTION | Opened pages were kept even when the snippet and the body disagreed; the disagreement was a flag, not a reason to drop the hit. |
| CLAIM CLASSIFICATION | Missing evidence was stored as `CONTRADICTED`. |
| GROUNDING | Removed sentences were counted as unsupported and not repaired. |
| SUPERVISOR | Independent checks did not schedule a repair. |
| MANAGER | `ESCALATE` outranked `CLARIFY` and skipped the approval insert. |
| STATE MACHINE | No `AWAITING_APPROVAL` transition was required for that path. |
| OTHER | Covered where a row does not fit the classes above. The archive has now been read; see the forensics document. |

## v2 response in this tree

The typed boundary, grammar mask, noise filter, repair loop, and approval insert are in the engine. The traceability table in `docs/V2_REAL_GPU_FORENSICS.md` maps each archive failure to a test that passed locally. They have not been confirmed on a new GPU run.
