# Claim ledger

Stored per work package in `claims` and `claim_evidence`.

| Field | Meaning |
| --- | --- |
| id | Claim id |
| package_id | Owning work package |
| agent_id | Who recorded it |
| claim_text | The sentence that may be published |
| claim_type | CALCULATION, AMBIGUITY, ASSUMPTION, MISSING_INFORMATION, ROUTE, FACT, PROSPECT, SOURCE_FAILURE |
| source_id | Reserved for a source row |
| evidence_text | The passage or the expression that supports the claim |
| source_url | Opened URL, if any |
| source_type | PRIMARY_OFFICIAL, HIGH_QUALITY_SECONDARY, OTHER_SECONDARY, COMMUNITY, UNKNOWN, DETERMINISTIC, INPUT |
| retrieved_at | When the evidence was captured |
| freshness | LIVE, FIXTURE_SNAPSHOT, INPUT, UNKNOWN, STALE |
| verification_status | PENDING, CHECKED, or CHALLENGED |
| confidence | From rank and freshness. Never from a model's self-score |
| challenged_by, challenge_reason | Set when an audit disputes a claim |
| supersedes | Earlier claim id, if this one replaces it |
| status | SUPPORTED, PARTIALLY_SUPPORTED, UNVERIFIED, CONTRADICTED, STALE |

## Rules that are enforced

- A snippet is at best PARTIALLY_SUPPORTED and confidence is capped.
- Availability language in a non-live retrieval is STALE and is not a current stock claim.
- Door totals are SUPPORTED only as arithmetic under an explicit scenario. They are not a quote.
- "Labour is ambiguous" is a supported claim about the input, not a guess.
- Missing specification fields are supported claims that the input does not contain them.
- Unsupported claims do not enter the published briefing. The briefing is rendered from the ledger.
- The verifier checks calculation claims against a fresh evaluation. The supervisor does not trust the employee's total as an input to arithmetic.

Status: **REAL** in SQLite. Fixture-backed route claims are honest about `FIXTURE_SNAPSHOT` and are not live web proof.
