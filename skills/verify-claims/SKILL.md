---
name: verify-claims
description: Check that each material claim is tied to evidence and downgrade anything that is not.
version: 1.0.0
triggers:
  - verify
  - claim
  - evidence
---

# Verify claims

A claim without evidence is not knowledge.

Statuses: SUPPORTED, PARTIALLY_SUPPORTED, UNVERIFIED, CONTRADICTED, STALE.

- Confidence follows the source rank and whether the page was opened. A model's own confidence is not a score.
- A snippet alone is at best PARTIALLY_SUPPORTED.
- Current facts from a frozen snapshot are not live. Availability language in a snapshot is STALE.
- Numbers in a claim must be in the evidence or in a deterministic calculation.
- If two supported claims disagree, keep both and mark the contradiction. Do not pick a side silently.
- Remove unsupported sentences from the published briefing. Put them in the gap list instead.
