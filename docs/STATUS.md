# STATUS

CURRENT PHASE: v2.0.0 local control plane. `VALIDATION_STATUS` is `V2_LOCAL_CONTROL_PLANE_GPU_PENDING`. `GPU_VALIDATED` is false. Model intelligence is unverified.

WHAT WORKS
- FastAPI campus at /campus, including a status brief (doing, why, stage, stuck, needs you, finished, trust)
- Work packages, hierarchy, workflow states, approvals, distribution
- Typed tool boundary: reasoning channel discarded; executable calls validated before they run
- Research filter (entity targets, authority class, rerank, noise rejection) on the production research path
- Claim ledger, repair after a disproved claim, employee self-check, supervisor tools, manager judgement with a safety veto
- Approval row when escalation still requires a person
- Local pytest (73 passed) against fixture pages and stub models
- Escalation disabled unless `AYVEN_ALLOW_ESCALATION=1`
- Code execution disabled unless `AYVEN_ALLOW_CODE=1` and the role is approved
- GPU banner and export gate: STOP POD only after a verified remote export

NOT YET
- A real Qwen rerun of the three exams. Model intelligence is untested.
- Crawl4AI, DSPy, Langfuse, Garak, llm-guard, and a PDF extractor are optional and not installed.
- OpenHands, Aider, SWE-agent, Letta, Mem0, LiteLLM, LangGraph, and Microsoft Agent Framework are not installed. Reasons are in `docs/INTELLIGENCE_COMPONENTS.md` and `docs/FRANKENSTEIN_READINESS.md`.
- Repair `RESEARCH_MORE` records a targeted question. It does not start a second full research programme.

NEXT ACTION
- Lee runs `./scripts/run_ayven_validation.sh` on a pod he already has. Preflight aborts before model download if core components or llguidance are down. Do not stop the pod until export status is VERIFIED. The engineering handoff is `docs/V2_ENGINEERING_HANDOFF.md`.
