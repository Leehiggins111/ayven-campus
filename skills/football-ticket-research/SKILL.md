---
name: football-ticket-research
description: Find legitimate ticket routes from official club pages and separate them from resellers.
version: 1.0.0
triggers:
  - ticket
  - dortmund
  - ajax
  - sparta
  - rosenborg
---

# Football ticket research

For each named club:

1. Search for the club's own ticket page first.
2. Open it. Record the official URL only if the fetch succeeded.
3. Call that channel official only when the host is the club or its official ticket domain.
4. A reseller, marketplace, or fan forum is not an authorised partner unless the official page says so. Do not invent a partnership.
5. Do not claim that tickets are currently on sale, in stock, or reserved. A snapshot, a navigation label, or model memory is not live availability. Mark sale language in a snapshot as stale.
6. A guessed path that 404s is a retrieval failure, not a route.
7. State the enquiry gaps: membership rules, away allocation, travel-package allocation, and whether a group can buy at all.
8. Do not buy, reserve, or contact the club.
