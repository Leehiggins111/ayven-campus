# STATUS

CURRENT PHASE: v1.0.0 intelligence engine — architecture and local tests. GPU validation pending.

WHAT WORKS
- FastAPI campus at /campus
- Work packages, hierarchy, approvals, distribution
- Intelligence loop: plan, skills, tools, claims, critic, verifier, supervisor, manager
- Local pytest against fixture pages and stub models
- Escalation disabled unless `AYVEN_ALLOW_ESCALATION=1`

NOT YET
- A real Qwen rerun of the three exams
- Browser Use, Firecrawl, and the MCP SDK are not installed

NEXT ACTION
- Lee runs `./scripts/run_ayven_validation.sh` on a pod he already has, then downloads the archive before stopping it.
