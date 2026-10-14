# Ayven — Claude handover (2026-10-14, ~20:40 BST)

Owner: Lee Higgins. No secret values are in this file, only variable names.

## 1. Vision (plain English)
Lee approves an **objective, scope and budget**. The system then assigns workers, does the work, checks it independently, corrects failures, saves usable results and reports back. Lee should not have to pass messages between agents.
- **Ayven**: the operating system and workforce, as a whole. The old `Ayven-OS` repo is a legacy client that only reads status and sends retries. Milo PR #23 recommends retiring it and does not delete it.
- **Milo** (`Leehiggins111/Milo`): Lee's interface (WhatsApp/Twilio + Electron + local voice). It keeps the brief, submits `POST /milo/jobs` to Campus, polls Campus, and shows approvals and results.
- **Campus** (`Leehiggins111/ayven-campus`): coordination. FastAPI job API, planning, employee and review stages, approvals, budget ledger, durable recovery (DBOS + libsql), and the HQ UI.
- **Workbench** (local, `/opt/ayven-workbench` on the laptop): worker execution on a local model (Ollama) with dev, reviewer and QA roles, a bubblewrap sandbox, and a panel at 127.0.0.1:8765.
- **Missing link:** there is no code bridge yet that runs Campus jobs on Workbench workers. A prototype branch exists at `/workspace/inv/workbench-bridge` (branch `bridge/task-contract`, `0231a7d`, local only, no remote). Unverified.

**Current position:** Campus and Milo are feature-complete on draft PRs and pass tests, but only with stand-in or mock models on the agents' box. Nothing new is deployed. Live Render still runs v2.0.2. No real-model or provider-billed job has ever completed end to end.

## 2. Components

### Campus
- PR #6 is draft, open and not merged. Head `cursor/ayven-workforce-fe56` = **70250c1** (confirmed through GitHub). Base is `cursor/ayven-v2-workable-a9eb`. Box checkout: `/workspace/campus/pr6-fix`.
- **Verified by QA, mock model, box, CI #267/#268 at d198768:**
  - The four faults (downloadable files, failed steps blocked from approval, scored criteria, exact GBP budgets) were FIXED-VERIFIED from 77adbdf onwards. Failed reports are leak-safe (77adbdf).
  - Budget: ledger rows match model calls 12 of 12. Small caps never overshoot. A slow-drip call times out and fails in 18 s (it was over 152 s). A kill and restart does not re-charge completed calls. **FIXED-VERIFIED (mock).** Product & Dev reported CI green with 483 passed.
  - These are **recorded mock costs, not provider billing.** There has been **no provider billing at all**: every run was mock or local.
  - Conditions before paying customers: set `AYVEN_AGENT_RUNTIME=native` in production, because qwen-agent calls are not confirmed as metered. Rerun the 5 browser tests in a normal environment.
  - Cosmetic defect (open): a second package stays "under review" on rejected or completed jobs.
- Reports: `/workspace/campus/repo-inspection/qa-campus-faults.md`, plus `docs/HANDOVER.md` and `docs/REQUIREMENTS_MAP.md` in the repo.
- **Live:** Render runs v2.0.2 (`dccfd28`). `AYVEN_ACCESS_KEY` is set and access protection is verified. PR #6 is **not deployed**.
- **Database:** Turso `ayven-campus` (libsql://ayven-campus-leehiggins111.aws-eu-west-1.turso.io, group `ayven-production`, delete protection on). **No token exists yet.** Hosted mode (`RENDER` set or `AYVEN_REQUIRE_DURABLE=1`) refuses to boot without `AYVEN_DB_URL` and `AYVEN_DB_AUTH_TOKEN`.
- Untested: any real model, the GPU run (a $1.10 cap is awaiting Lee's approval), hosted restart and redeploy acceptance.

### Milo
- PR #23 is draft, open and mergeable (clean). Head `cursor/milo-ayven-interface-297f` = **5b59e15** (GitHub). Base is `codex/milo-ayven-acceptance-repairs`. 321 tests were **reported** (`npm test`).
- Milo-to-Campus acceptance ran against Campus at 4a2d946, locally, and was **SIMULATED** only. Milo labels these runs `simulated_complete` and never marks them done.
- The local CPU voice turn (faster-whisper + Kokoro) passed on the box. Microphone and speaker are device-dependent.
- Cloud Run hosting is **reported, unverified**. Docs: `docs/HANDOVER-AYVEN.md` and `docs/MILO_CONTRACT.md` (Campus repo). Env names: `CAMPUS_BASE_URL`, `CAMPUS_ACCESS_KEY` (at least 32 characters), `VOICE_PAID_WHISPER`, `VOICE_WHISPER_MODEL`, `VOICE_KOKORO_VOICE`. Legacy names: `AYVEN_API_BASE_URL`, `AYVEN_API_SECRET`, `AYVEN_MILO_WRITE_SECRET`.
- The Campus defects listed in PR #23 (bundle 404, criteria empty, $2 clamp) were later fixed in Campus (the four faults above). "Database is locked" on file SQLite should be rechecked.

### Workbench
- **Box copy:** `/workspace/campus/workbench/src` (git, master `666942f`, a snapshot of the laptop install, no remote). Working repo: `/workspace/campus/workbench/repo` (master **edf29bc**, branch **role-models 68e2e40**, origin = `../src`). **There is no GitHub repo** for Workbench (searched), so it was archived (section 9).
- **Last QA at edf29bc:** all defects fixed (SIMULATED).
- **Real model, box CPU, qwen3:4b:** feature and bug fix passed 3/3. Review failed 0/3, and QA correctly blocked it.
- **Capability study:** `/workspace/campus/workbench/capability-study/REPORT.md` (also committed on `role-models`). Finding: no tested free local model is good enough as reviewer or QA on its own. qwen3:4b-instruct got 1/8 strict passes. Keep the strict answer-key check and add a human acceptance step. Hardware is **not justified** by these measurements.
- **Laptop** (PENDING-LAPTOP, not touched in this handover): `worker.py` has thinking off (backup `worker.py.bak-pre-overnight`). `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0` are set. `qwen2.5-coder:3b` is pulled. **The box copy (edf29bc / role-models) has NOT been installed back on the laptop.**
- Docs: `HANDOVER-OVERNIGHT.md`, `BOARD.md`, `qa.md`, `research-models.md`, `hosting-options.md`, `qa-evidence/` (under the workbench folder).

### Defect status (checked by grepping code and QA files)
- **The acceptance check ignores ACCEPT/REJECT contradictions: OPEN.** `team.py check_review` only verifies file:line citations. Neither `master` nor `role-models` contains verdict-parsing code (git grep finds ACCEPT only in a prompt string). The fix was proposed in REPORT.md and **not made**.
- **Unreliable local reviewer: OPEN** (a model limit, measured above). The mitigation is configured as a proposal only.

## 3. End-to-end trace (Milo instruction → saved, verified result)
1. Lee gives Milo the brief. The brief is stored. **Works locally.**
2. Milo sends `POST /milo/jobs` to Campus (`CAMPUS_BASE_URL`, `CAMPUS_ACCESS_KEY`). **Simulated locally only. Not done against a hosted Campus.** Missing: Render running PR #6, plus the DB variables.
3. Campus plans the job, enforces the budget and asks Lee for approval. **Mock-verified.**
4. Workers do the work. **Missing link:** Campus calls its own model runtime (native/qwen-agent/Ollama), not Workbench. The Campus↔Workbench bridge is a prototype only. A real model has never been run in Campus.
5. Independent check. Campus review stage: mock-verified. Workbench reviewer: unreliable, and the verdict contradiction check is OPEN.
6. Correct failures. Retries and repairs: mock-verified.
7. Save usable results (`deliverable.md` and the bundle zip). Mock-verified. Hosted durability is unproven.
8. Report back to Milo (polling, approvals, voice status). Simulated.

## 4. Remaining work in dependency order
**Access / config (Lee):**
1. Create a Turso token scoped to `ayven-campus`. Done when the token exists, stored only in Render.
2. In Render env, set `AYVEN_DB_URL`, `AYVEN_DB_AUTH_TOKEN` and `AYVEN_AGENT_RUNTIME=native`. `AYVEN_ACCESS_KEY` is already set. Done when the variables are present.
3. Confirm Render has no card on file (no surprise billing).
4. Merge PR #6, or point Render at the branch, then redeploy. Done when `/health` reports `durable`.

**Software:**
5. Prove restart and redeploy acceptance on Render, following `docs/HANDOVER.md` (survives restart and redeploy, interrupted work resumes, review runs once).
6. Rerun the 5 browser tests in a normal environment.
7. Fix the second package that stays "under review" (cosmetic).
8. Workbench: fail a review that says ACCEPT while listing defects that match the key. Done when a regression test exists in which an ACCEPT review with key-matched defects fails the check.
9. Campus↔Workbench bridge: finish or decide on `bridge/task-contract`.
10. Milo against hosted Campus: one REAL job, labelled HOSTED.

**Model limits:** local 4B models can't review reliably. Keep the human acceptance step, or approve a paid or larger reviewer.

**Lee decisions:**
- Approve or decline the GPU real-model run ($1.10 cap).
- Reinstall Workbench on the laptop (edf29bc + role-models).
- The four decisions from COMPARISON.md, verbatim:

| # | Decision | Facts | Status |
|---|---|---|---|
| D1 | Always-on VPS, about £17/mo (Hetzner CPX22 £16.90 + backups = £17.43) | DocuSeal, Twenty, n8n, Medusa and the LiteLLM proxy all need durable storage. Render free loses SQLite on restart, and its free Postgres expires after 30 days. A 4 GB VPS is **too small for self-hosted Postiz** | NOT AUTHORISED |
| D2 | Our own receptionist booking engine vs client-paid Cal.com Teams | Cal.diy has Teams removed and is not for production. Cal.com Teams is $12/user/mo (yearly), with the account held and paid by the client. Our AI-Receptionist engine already models staff rotas with human confirmation | NOT AUTHORISED |
| D3 | Postiz hosted (£17.41 annual / £21.96 monthly) vs self-hosted on your laptop | The laptop (16 GB) is fine for trials but isn't always-on. A self-host VPS is ≈ £34+ (ASSUMPTION). Either way you must own each platform developer app, and Meta and YouTube take more than a month | NOT AUTHORISED |
| D4 | DataForSEO account for an OpenSEO test | Pay as you go, $50 minimum top-up, $1 trial credit for new accounts. Opening the account is itself an account action under the rules | NOT AUTHORISED |

Repo inspection, top five: Codebase Memory MCP (TESTED), ECC with Agency Agents (TESTED by hand), DocuSeal (TESTED with caveats, see sales.md), Postiz (NOT RUN), Promptfoo (CANDIDATE). See `/workspace/campus/repo-inspection/COMPARISON.md` (and `.pdf`).

## 5. Hosting (names only)
- Render (Campus): `AYVEN_ACCESS_KEY` (set), `AYVEN_DB_URL`, `AYVEN_DB_AUTH_TOKEN`, `AYVEN_AGENT_RUNTIME`, optional `AYVEN_REQUIRE_DURABLE`. All are set in the Render dashboard, Environment tab.
- Milo (Cloud Run, unverified): `CAMPUS_BASE_URL`, `CAMPUS_ACCESS_KEY`. Set them in the service's env.
- Detail: `/workspace/campus/ayven-audit/impl/hosting.md` and `workbench/hosting-options.md`.

## 6. Hardware (proposals only)
Laptop: i5-11400H, 16 GB, RTX 2050 4 GB. The study says faster hardware wouldn't fix reviewer quality. Any GPU or VPS spend needs Lee's approval (see D1 and the GPU run).

## 7. Locate and run
- Campus: `cd /workspace/campus/pr6-fix/apps/api && pytest tests` (mock). Hosted: Render.
- Milo: clone `Leehiggins111/Milo`, branch `cursor/milo-ayven-interface-297f`, then `npm test`. Other box clones: `/workspace/Milo*`, `/workspace/milo-canonical`.
- Workbench: on the laptop, WSL `/opt/ayven-workbench`, panel at 127.0.0.1:8765, Ollama `qwen3:4b-instruct`. On the box: `/workspace/campus/workbench/repo`.
- Audit docs: `/workspace/campus/ayven-audit/` (MISSION, CHECKLIST, impl/, fullscope/).

## 8. Known failures
- The ACCEPT/REJECT contradiction is OPEN.
- The local reviewer fails (0/3; 1/8 strict).
- Qwen-agent metering is unconfirmed.
- The second package stays "under review".
- The browser tests haven't run in a normal environment.
- Hosted Campus isn't on PR #6.
- No Turso token.
- Cursor cloud agents are unavailable (Lee won't pay on-demand). Use the GitHub connector and box sandboxes.

## 9. Preservation (done 2026-10-14)
- Campus box checkout: regenerated screenshots (8 PNGs, uncommitted) were committed locally to branch `handover/2026-10-14` (`8671ea9`). Patch: `handover/0001-Handover-preserve-regenerated-screenshots-uncommitte.patch`. Not pushed.
- Workbench: `handover/workbench-src.tgz` (src + repo + capability-study, model files excluded) and `handover/workbench-repo-all-branches.bundle` (master + role-models).
- All other repos under /workspace were clean (Milo clones, `inv/workbench-bridge`, `inv/dogfood/campus-fault4`, qa-src, ayven-audit-qa, inv clones). The third-party checkouts under repo-inspection were not touched.

## 10. Claude's first five tasks
1. Once Lee has done steps 1–4 in section 4, run the Render restart and redeploy acceptance. Record it in `docs/HANDOVER.md`.
2. Implement and test the Workbench verdict contradiction check (section 4, item 8).
3. Rerun the 5 browser tests. Fix the "under review" package.
4. With Lee's approval only, run the GPU real-model job ($1.10 cap). Compare the ledger with provider billing.
5. Finish the Campus↔Workbench bridge contract. Then run one REAL Milo job against hosted Campus.

## 11. Completion demonstration script
1. **Real success:** Milo submits a brief with a £ budget. Lee approves. Workers run on a real model. The review passes. `deliverable.md` and `bundle.zip` download and are usable. Milo reports it labelled HOSTED, not SIMULATED.
2. **Rejection of bad work:** submit a task with a planted defect. The reviewer says REJECT, or a contradictory ACCEPT is failed. The job is blocked from approval with stage, cause and next action.
3. **Restart recovery:** kill or redeploy Render mid-employee-stage. The job resumes, the review runs once, and completed calls are not re-charged.
4. **Budget enforcement:** a small cap stops the job without overshoot. A slow call times out. The ledger matches provider billing.
5. **Saved outputs:** after the restart, the outputs are still retrievable from Turso-backed storage.
