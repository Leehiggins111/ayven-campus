# STATUS

CURRENT PHASE: v1.1.0 Frankenstein runtimes — local integration tests. GPU validation still pending.

WHAT WORKS
- FastAPI campus at /campus
- Work packages, hierarchy, approvals, distribution
- Intelligence loop with Qwen-Agent tool calling, selective skills, Browser Use (read-only), MCP stdio, ranked SQLite memory, and an unshare code sandbox
- Claim ledger, grounding, critic, verifier, supervisor tools, manager judgement with a safety veto
- Local pytest against fixture pages and stub models
- Escalation disabled unless `AYVEN_ALLOW_ESCALATION=1`
- Code execution disabled unless `AYVEN_ALLOW_CODE=1` and the role is approved

NOT YET
- A real Qwen rerun of the three exams. Model intelligence is untested.
- OpenHands, Aider, SWE-agent, Letta, Mem0, LiteLLM, LangGraph, and Microsoft Agent Framework are not installed. Reasons are in `docs/FRANKENSTEIN_READINESS.md`.

NEXT ACTION
- Lee runs `./scripts/run_ayven_validation.sh` on a pod he already has. If a core capability is inactive, the script stops before model download. Download the archive before stopping the pod.
