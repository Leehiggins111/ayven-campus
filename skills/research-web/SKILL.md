---
name: research-web
description: Decompose a question, search, open pages, and keep only evidence that was actually retrieved.
version: 1.0.0
triggers:
  - research
  - source
  - current
---

# Web research

Use this process. Do not replace a failed search with memory.

1. Split the objective into separate questions. One club, one supplier, or one statistic is one question.
2. Search, then triage. Drop social-video results unless the question is about that community.
3. Open the page. A snippet is a lead, not evidence, once the page can be opened.
4. Keep a short passage that contains the fact. Record the URL, title, retrieval time, and source rank.
5. Rank sources: PRIMARY_OFFICIAL, HIGH_QUALITY_SECONDARY, OTHER_SECONDARY, COMMUNITY, UNKNOWN.
6. If the fetch fails or the page is only a JavaScript shell, record the failure. Do not invent the page.
7. If a named entity still has no opened page, search once more, then stop and report the gap.
8. Do not state live availability, prices, or partnerships that the opened text does not support.
