# Real GPU forensics — run ayven-validation-20260928T102746Z

Source: the preserved v1.1.1 archive (database, metrics, scorecard, benchmark cards, and the 9 employee / 9 supervisor / 3 manager outputs). This document replaces the “artifacts were not on disk” section of `docs/V2_GPU_FAILURE_ANALYSIS.md`. The code-path notes in that file stay as background. Where they disagree with a row below, this file wins.

No exam name was added to engine Python to produce these fixes. The raw strings live in `apps/api/tests/fixtures/gpu_archive/contaminated_queries.json`.

## Headline numbers

The archive metrics match the reported scorecard exactly.

| Item | Archive |
| --- | --- |
| Run | `ayven-validation-20260928T102746Z` |
| GPU | L40, dry_run false, require_real true |
| Verdict | FAIL |
| Model calls | 21 (EMPLOYEE, SUPERVISOR, MANAGER all real; stub roles empty) |
| Tool calls | 90 |
| Sources opened | 56 |
| Claims created | 131 |
| Claims challenged | 131 |
| Claims rejected (scorecard) | 24 (trades 8, football 11, vending 5) |
| Retries | 0 |
| Tokens | 5147 |
| Latency | 631.26s |
| Estimated GPU USD | 0.351 at $2/hour |
| Approvals | 0 |
| `workflow_state` column | absent (v1.1.1 schema) |

The scorecard word “rejected” is not a claim status. `validation/harness.py` sets `claims_rejected` to:

`count(claims.status == CONTRADICTED) + len(observability.unsupported_removed)`

That is 18 + 6 = 24. The database statuses are different and are not a contradiction of the scorecard:

| Claim status | Count |
| --- | --- |
| SUPPORTED | 89 |
| UNVERIFIED | 22 |
| CONTRADICTED | 18 |
| PARTIALLY_SUPPORTED | 2 |
| Total | 131 |

Supervisor challenge payloads: **STOOD 116, DISPROVED 15**. The 15 DISPROVED rows are the dictionary `SOURCE_FAILURE` claims (a failed fetch whose error text contained a URL). The other 3 CONTRADICTED rows were marked by the independent page check (`Independent page contradicted the claim`) and were not in the DISPROVED list. The 6 `unsupported_removed` sentences were stripped before publication and were never challenged. 15 + 3 + 6 = 24.

Tool mix: fetch_page 72 (56 ok, 16 error), web_search 14 (9 ok, 5 empty), calculator 3 ok, record_review 1 ok.

Every employee package has `attempt_count` 1 and supervisor decision ACCEPT. Parent packages are `escalation_required`, `requires_approval` 0, manager decision ESCALATE. Repairs key is absent. Retries in observability are 0.

## What the run actually did

Three searches used the query string `<think>` (one per exam). Those searches opened dictionary pages (403), `trade.thinkorswim.com`, `thinkproducts.com`, `thinkbank.com`, `vocabulary.com`, Wikipedia’s Think slogan, and `englishverbs.net` (which embeds `think.mp3`). Other searches used the model’s planning paragraph as the query (`Okay, let's tackle this query…`, `First, the objective mentions…`, `The search term would be…`). Several of those returned empty because the query was the paragraph. Football therefore never opened the clubs’ own sites. Pages that said “official” were stored as `PRIMARY_OFFICIAL` routes, including a protein-bar shop whose HTML (stylesheet link included) became the claim text.

Raw role files still contain `<think>` blocks. Published findings do not contain the tag. Football findings do contain `think.mp3` inside an opened-page extract, which is why `football.urls_retrieved` failed. `common.no_think` failed because the stored observability still held the query `<think>`.

Customer-stated `Door: £82` was removed as unsupported even though the brief states it. Four club URLs written by the channels employee were removed because those URLs had not been opened. That removal was correct. Nothing then searched for a replacement. The vending draft’s `example.org/estates` link was removed. That removal was correct. The vending card stayed PARTIAL (`useful_findings` 0.5, `prospect_count` 1) because the opened pages were market reports from a planning-prose query, not evidenced placement prospects.

Managers: trades model proposed CLARIFY and the safety veto forced ESCALATE (`veto-forced`). Football’s model proposal was empty (`safety-only` ESCALATE). Vending’s model proposed RETURN and the veto forced ESCALATE. All three say research was exhausted. No approval row was inserted. The football routes supervisor wrote RETURN; the stored decision was ACCEPT because the rendered page still contained the word “official”. The disagreement was logged and did not retry.

## The 24 scorecard rejects

Columns: (1) work package (2) employee output (3) claim (4) evidence (5) supervisor challenge (6) supervisor result (7) manager handling (8) why it failed (9) was the rejection correct (10) root-cause class.

Manager handling is the same for every row in a benchmark. It is repeated so each row stands alone.

Dictionary rows share one mechanism. Each is a `SOURCE_FAILURE` claim whose text is `Retrieval failed at fetch: 403` for one of three URLs (`dictionary.cambridge.org/dictionary/english/think`, `merriam-webster.com/dictionary/think`, `merriam-webster.com/thesaurus/think`). Evidence JSON query is `<think>`. Challenge: “was this URL opened”. Result: DISPROVED, reason “The URL is not in the opened evidence.” The 403 response was never stored as a source, so the check treated a true failure report as a contradicted fact. The rejection of the row as CONTRADICTED was not correct. The fetch itself should not have run.

| # | Benchmark / package | Employee file | Claim | Challenge result | Rejection correct? | Class |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Trades / Measurement and specification gaps | `A_trades-59c3e2db.md` | Cambridge 403 | DISPROVED, URL not opened | No. A failed fetch is a gap | reasoning leakage, then claim-classification error |
| 2 | same | same | Merriam-Webster dictionary 403 | same | No | same |
| 3 | same | same | Merriam-Webster thesaurus 403 | same | No | same |
| 4 | Trades / Supplier evidence | `A_trades-995cb028.md` (saved prose empty; claim came from the fetch) | thinkproducts HTML stored as ROUTE, source rank PRIMARY_OFFICIAL | Independent page contradicted the claim | Yes, the page is not a supplier. The numeric check was accidental | reasoning leakage, weak source selection, extraction failure |
| 5–7 | Trades / Supplier evidence | same | the three dictionary 403s | DISPROVED, URL not opened | No | reasoning leakage, claim-classification error |
| 8 | Trades / Measurement gaps employee prose | `A_trades-59c3e2db.md` | `Door: £82` in `unsupported_removed` | not challenged; stripped before publication | No. £82 is in the customer brief | grounding error |
| 9 | Football / Official ticket routes | `B_football-a1fbcc05.md` | thinkproducts HTML as a ticket route, PRIMARY_OFFICIAL | Independent page contradicted | Yes, it is not a ticket route | reasoning leakage, weak source selection, extraction failure |
| 10–12 | Football / Official ticket routes | same | the three dictionary 403s | DISPROVED | No | reasoning leakage, claim-classification error |
| 13–15 | Football / Enquiry gaps | `B_football-1d3a4e0b.md` | the three dictionary 403s | DISPROVED | No | same |
| 16 | Football / Official versus reseller | `B_football-0fe75def.md` | “Borussia Dortmund's official site is https://www.borussiadortmund.com/” removed | not challenged | Yes. That URL was not opened | unsupported synthesis after poor query planning |
| 17 | same | same | Ajax `ajax.nl` removed | not challenged | Yes | same |
| 18 | same | same | Sparta `spartapraha.cz` removed | not challenged | Yes | same |
| 19 | same | same | Rosenborg `rosenborg.no` removed | not challenged | Yes | same |
| 20 | Vending / Evidenced placement prospects | `C_vending-67c642ac.md` | thinkproducts HTML as a placement route | Independent page contradicted | Yes, it is not a placement prospect | reasoning leakage, weak source selection, extraction failure |
| 21–23 | Vending / Decision-maker and missing information | `C_vending-6a3ee37a.md` | the three dictionary 403s | DISPROVED | No | reasoning leakage, claim-classification error |
| 24 | Vending / Outreach draft only | `C_vending-34f92a1f.md` | “Fetch pages from organisations… example.org/estates” removed | not challenged | Yes. The URL was invented | unsupported synthesis |

Manager handling for 1–8: model CLARIFY, safety ESCALATE, `decision_source` veto-forced, `requires_approval` 0, approvals table empty, stage `escalation_required`. For 9–19: model proposal empty, safety-only ESCALATE, same approval miss. Football routes advisory was RETURN and the stored supervisor decision was ACCEPT. For 20–24: model RETURN, veto-forced ESCALATE, completion PARTIAL, same approval miss.

The 22 UNVERIFIED rows are mostly the same pattern one step earlier: a planning paragraph was searched or fetched (SQL tutorials and calculator sites on trades, ticket blogs and 404/429 pages on football, market-report 403s on vending) and stored as `SOURCE_FAILURE` with no challenge reason. They are not in the 24. They are the same query-planning failure. Two PARTIALLY_SUPPORTED rows are the hinge assumption and one vending inference. Those were labelled and were not scorecard rejects.

## Traceability

Every test below exists and passed in the local suite (`85 passed`).

| REAL GPU FAILURE | ROOT CAUSE | V2 FIX | TEST PROVING FIX | STATUS |
| --- | --- | --- | --- | --- |
| Query `<think>` executed as web_search | reasoning leakage into the tool argument | Planning text is split into channels. A reasoning marker is not a query, and a rejected query is stored as `[rejected-reasoning]` | `test_archive_think_queries_and_media_cannot_execute` | PASS |
| `think.mp3`, dictionary `/think`, stylesheet URLs opened or kept in the claim | malformed/contaminated tool call and extraction failure | Noise filter on media suffixes and path stems; tags and those URLs are stripped before a page becomes a claim | same test | PASS |
| Multi-sentence “let’s tackle / the query would be” paragraphs used as the search | poor query planning | Planning prose is rejected. A short quoted search inside it is kept. If nothing safe remains, queries fall back to names in the objective | same test | PASS |
| A page that says “official” ranked PRIMARY_OFFICIAL, including the snack shop | weak source selection | Official rank requires the host label in the page together with a self-identification, or the query token in the host. The word official alone does not | same test, and `test_research_stale_primary_secondary_js_irrelevant_reseller_and_follow` | PASS |
| 403 failure text contradicted because the URL was “not opened” | claim-classification error | `SOURCE_FAILURE` is a gap. It is not a contradicted fact | `test_source_failure_is_a_gap_not_a_contradiction` | PASS |
| Snack-shop HTML kept as a route and never repaired | extraction failure, then no repair | A route that names none of the requested entities is disproved, researched with an entity query, written into the section, and rechecked | `test_archive_route_is_rejected_researched_rewritten_and_rechecked` | PASS |
| Rejected material produced a gap and then stopped | Supervisor/repair stopped at a question | A follow-up search that opens nothing is `UNRESOLVED_GAP` after the search ran | `test_archive_route_stays_unresolved_when_research_finds_nothing` | PASS |
| `Door: £82` stripped though the brief states it | grounding error | Customer prices and the objective are part of the grounding corpus | `test_customer_stated_price_stays_when_it_is_in_the_input` | PASS |
| Invented URL in the vending draft | unsupported synthesis | Specifics absent from evidence are removed. A removed sentence with a named entity is sent through the same research repair | `test_output_grounding_strips_unsupported_specifics_and_keeps_labels` | PASS |
| 0 retries while claims were rejected | repair did not run | Disproved material claims call the capped repair, which increments retries | `test_research_repair_supports_the_claim_from_a_fixture_source` | PASS |
| ESCALATE with approvals 0 and no workflow state | Manager/state-machine error | Escalation that still needs a person inserts an approval and moves to `AWAITING_APPROVAL`. Resolve moves it to `APPROVED` | `test_approval_record_exists_for_clarify_and_for_escalation` | PASS |
| Vending PARTIAL after safe refusal | research never reached a named prospect because the query was prose | Prose is not searched. Coverage still depends on a real page. Empty evidence stays a gap rather than an invented prospect | `test_empty_vending_is_safety_pass_and_completion_fail` | PASS |

## What this does not prove

These tests replay the archive’s contaminated strings through the v2 boundary and the repair loop with fixture pages. They do not re-run Qwen on an L40. Football source discovery on the live web, and whether the next scorecard’s repair rate is above zero, remain model-unvalidated.
