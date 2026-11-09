# Security review — integrated release candidate (25 Oct 2026, Claude Code)

Scope: the integrated candidate Milo `c2ba031` / Campus `056e52b` plus the hardening on
`security/release-hardening-20261024`. This is a review of the code as it stands, with the fixes I made noted
inline. It is **not** a sign-off to enable live owner approvals or to deploy; those remain owner decisions after
Q&T's independent GO.

Legend: **[fixed]** addressed on this branch; **[ok]** reviewed, no change needed; **[contained]** a real risk held
in check by a control, with residual noted; **[owner]** needs an owner action or decision.

## A. Execution lane (Workbench + Campus)
- **[fixed] Sandbox capabilities.** As root the jail kept all capabilities (`CAP_SYS_ADMIN` → `mount`, `mknod`).
  `--cap-drop ALL`; proven by `SandboxIsolation` (mount/mknod refused, `CapEff=0`). See CLAUDE-CODE-HARDENING.md §1.
- **[ok] Workspace isolation on the host.** `wsgit` pins the recorded git dir and never discovers a repo above the
  workspace, so an agent rewriting its own `.git` cannot redirect host git. Host-side file reads use `openat` with
  `O_NOFOLLOW`. **[fixed]** verification now also refuses a symlink/submodule patch before building a tree.
- **[ok] No unsafe shell.** `AYVEN_UNSAFE_SHELL` only ever disables the sandbox, and it is refused, not honoured;
  legacy engines (OpenHands/Hermes) that ran outside the jail are disabled with no env override.
- **[fixed] Forged PASS / provenance.** Campus derives REAL/SIMULATED from the Workbench-**signed** record, never
  from the worker; a stub endpoint is SIMULATED and unapprovable (D12, §3). The verification record is HMAC-signed
  with a key the worker never holds, so a worker-token holder cannot forge a PASS (pre-existing, re-confirmed).
- **[ok] Exact-version approval (D1, release).** Accept pins `patch_sha256`; `changes.diff` is released only while it
  still matches; the panel apply requires the reviewed digest == the live patch == the signed PASS record. Campus
  orders cannot be applied from the panel. `test_release_hash_pinning.py` and the `checks` apply test (updated).
- **[ok] Scope / `.git`.** Per-order exact allow-lists enforced at submission, during the run, at QA, at export and
  again at apply; `.git*`/`.github` refused case-insensitively (D9). Campus re-checks the patch independently.
- **[contained] In-process test runner.** Code under test shares the runner's process; deliberate introspection that
  evades the tamper scan could forge a report. Residual accepted for this release; the owner previews the exact
  patch, and `removed_symbols` plus the signed logs make a silent regression hard. An out-of-process oracle is the
  longer-term fix.
- **[contained] Shared attestation key.** Symmetric, held by the Workbench host and Campus. A full compromise of
  either can sign. Asymmetric keys would split that; owner decision.
- **[owner] Attestation key provisioning.** `AYVEN_ATTESTATION_KEY` must be set on the Workbench host and Campus
  only, never the worker. The worker and Campus both refuse to start if the key is reused as an access/worker token
  or given to the worker.

## B. Owner decisions and apply (Milo)
- **[ok] Local-only, fail-closed.** `owner-actions.js`: accept/reject/apply need `MILO_LOCAL_OWNER_DECISIONS=1`, a
  loopback bind, a loopback caller and a non-cloud deployment; cloud is refused unconditionally. Apply writes only
  into a directory under `MILO_DISPOSABLE_APPLY_ROOT` that carries a `.milo-disposable` marker and is a git repo,
  resolved with `realpath` and confined to that root (no `..`, no symlink escape); only the exact accepted bytes
  (re-hashed) are applied, into an otherwise-clean tree, uncommitted. This never touches a real project.
- **[ok] Approvals stay off.** The UI's approval controls are disabled in code; this review does not change that.
- **[owner] Enabling decisions.** Turning `MILO_LOCAL_OWNER_DECISIONS` on is an owner action after Q&T's GO; it is
  loopback/local-only and can never be set in cloud mode.

## C. P&D desktop companion (`desktop/`)
- **[ok] Default-deny policy.** `policy.py`: any load/parse error disables every capability; unknown keys rejected;
  `confirmation_required` must include `consequential` and cannot be lowered; `allowed_paths` rejects roots and
  <3-part paths; only `notepad`, only listed extensions.
- **[ok] Approval cannot come from the API.** `server.py` rejects any Origin/Sec-Fetch header, binds `127.0.0.1`
  only, requires a constant-time token, refuses chunked bodies and caps sizes. Consequential actions are confirmed
  on the companion's own console/dialog; there is no HTTP route that approves, and `approval` fields in a request
  body are rejected as unknown fields.
- **[ok] Hands path safety.** `hands.check_path`: absolute only, lexical==real (no `..`/symlink), name and extension
  allow-listed, must be **directly** inside an allowed disposable folder, must not already exist (create-only).
- **[contained] On-device input control.** "Hands" drives real keyboard/mouse via `xdotool` (Linux) / the Windows
  backend. This is powerful by nature; it is held by the default-deny policy, per-action owner confirmation, the
  create-only path rule, and the audit log. It has **not** been exercised on Windows on-device (owner action), and
  it is off unless a policy file enables it. Treat journald/console read access as companion access.
- **[ok] Audit.** Append-only `audit.jsonl` with a verify endpoint; the token is never logged.

## D. Researcher / specialists (`desktop/specialists`, `campus_ext/specialist_tasks.py`)
- **[ok] SSRF guard.** `specialists/tools.py` fetch: `https` only, no credentials in the URL, every resolved IP
  checked against private/loopback/link-local/reserved/multicast, and redirects re-checked. The RFC 2544 fake-IP
  range is refused unless an operator opts in with `MILO_FETCH_ALLOW_FAKEIP_EGRESS=1` (off by default). No cookies.
- **[ok] Developer specialist is the hardened lane.** `specialist_tasks.py` Developer tasks go through the same
  Workbench execution path (`cloud-demo`, exact allow-list, `AYVEN_WORKBENCH_SUBMISSIONS_ENABLED=1`); there is no
  specialist approval route — accept/reject stays on the hash-pinned local-only decision. Worker routes require the
  worker token.
- **[contained] `project_alias` hard-coded `cloud-demo`.** The specialist code fixes the alias; combined with the
  owner alias allow-list (D13) this is acceptable for the single-project slice, but a multi-project setup must thread
  the alias through rather than hard-code it. Noted for follow-up; not a release blocker for the single demo
  project.

## E. Secrets and configuration
- **[ok] No unsafe secret defaults.** Every token/key has no default and is length-checked; Campus fails closed
  outside local mode; the only hard-coded strings are placeholder API keys the local model server ignores.
- **[fixed] Test-only override is fenced.** `AYVEN_TEST_ACCEPT_UNVERIFIED_MODEL` (scripted-model drills) is refused
  at start-up unless `AYVEN_ENV=test`, and every result it affects is marked and keeps its true provenance.
- **[owner] Earlier infra findings stand** (from hosting SECURITY-FINDINGS): expired staging Turso token, staging
  wired to production secrets, public Cloud Run invoker, project-wide `secretAccessor`, open default SSH/RDP. All
  owner/deploy actions; none is in this release's local run.

## What I did NOT review in depth
- The Milo browser/OAuth sign-in internals and the desktop Electron packaging (owned elsewhere; I relied on their
  own tests).
- The Windows `hands` backend on real hardware (no Windows on-device run was performed here).
- The full Campus web/program job lane beyond confirming its failures are flaky and unrelated (§4 of hardening).

## Verdict
The hardened execution lane, provenance, logs and local-only decision/apply path are sound and tested, with the
residual risks above **contained and documented**. Remaining items are owner actions (key provisioning, enabling
decisions, infra) and the accepted residuals (in-process runner, symmetric key, on-device input, hard-coded alias).
No security control was weakened to make anything demonstrate. This is a security review, not a GO.
