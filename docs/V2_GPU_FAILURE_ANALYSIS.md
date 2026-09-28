# GPU run #2 failure analysis

Version under analysis: 1.1.1, commit `ab5869ade7c8a99905591131f447ba1c063b640c`.

## Artifacts

The preserved GPU run is **not in this repository or on this workspace**. Searches of `validation/`, `docs/`, `benchmarks/`, `/tmp`, and the home directory found no scorecard, metrics file, claim dump, model-output folder, or tool log from that run.

The figures below are the ones supplied with the v2 brief, not rows read from an archive:

| Exam | Completion | Grounding | Calc | Supervisor | Manager | Unsupported claims | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Trades | PASS | PASS | PASS | PASS | PASS | 8 | FAIL |
| Football | PASS | PASS | PASS | PASS | PASS | 11 | FAIL |
| Vending | PARTIAL | PASS | PASS | PASS | PASS | 5 | FAIL |

Totals supplied with the brief: 21 model calls, 90 tool calls, 56 sources opened, 131 claims created/challenged, 24 rejected, 0 retries, 631.26s, 5147 tokens, all roles REAL, about $0.35 inference. OVERALL FAIL.

A per-claim table (task, employee, claim text, originating model output, evidence, rejection reason) **cannot be written without inventing it**. The 24 rejected claims are not on disk.

### Files needed to finish the per-claim map

From the pod, before it is destroyed:

- `validation/runs/<stamp>/metrics.json`
- `validation/runs/<stamp>/final-report.md`
- `validation/runs/<stamp>/benchmarks/*.md` and `*.json`
- `validation/runs/<stamp>/employee/*.md`, `supervisor/*.md`, `manager/*.md`
- `validation/runs/<stamp>/ayven.db` (tables `claims`, `claim_evidence`, `tool_calls`, `model_calls`, `verification_results`, `approvals`, `work_packages`)
- `validation/runs/<stamp>/EXPORT.txt` if it existed

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

Unsupported specifics (a figure, URL, or organisation that no opened page states) **should** be rejected. The engine bug is what happened next: no repair, no retry, and often an escalation that dropped the approval row. Do not treat all 24 as model hallucinations until the claim rows are in the archive. Tool-routing junk (`think`, `think.mp3`) is a control bug even when the page exists.

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
| OTHER | The run archive lived on ephemeral pod disk, so the first detailed run could not be re-read. |

## v2 response in this tree

The typed boundary, grammar mask, noise filter, repair loop, and approval insert are in the engine. They are covered by local tests. They have **not** been confirmed against the missing 24 rows or a new GPU run.
