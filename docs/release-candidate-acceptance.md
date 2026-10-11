# Milo + Ayven candidate acceptance, 11 October 2026

Status: **not accepted; no installer delivered**.

Integrated Campus source: `ca4d93aa44f1d1bb2bff783ba7daaeb4797f607f`, tree `9aa49e53ee55fe8796b88f85729d04d53877a99e` (draft PR5). Matching private Milo source: `cfd478681731dea0fbea1c7526656728ead98ea5` (draft PR22). Subsequent changes on this candidate branch tighten the implementation prompt and record this acceptance ledger. No PR was merged and no Render service was changed.

## Evidence boundaries

| Requirement | Implementation and checks | Current result |
|---|---|---|
| Conversation and direct arithmetic | Milo operator and routing tests; restricted calculator; model greeting probe | Milo source suite 297 passed; direct local model greeting returned a usable reply. Installed application unverified. |
| Durable job identity, clarification, restart | Shared submit_job; durable engine; bridge, recovery and admission tests | Focused source tests passed. Actual Windows restart unverified. |
| Access protection and backups | Signed expiring sessions, admin bearer checks, SQLite backup API; access and release_handoff tests | Source checks passed. Representative Windows upgrade and rollback unverified. |
| Research and business writing | Existing opened-source pipeline plus relevant-evidence scoring and preserved drafts; business, deliverable and isolation tests | Source checks passed in focused suites. Alba, coffee and unseen-business installed acceptance outstanding. |
| Small Python software | Test-first builder, bounded retries, file checksums and persisted execution evidence; software_build tests | Latest software suite 10 passed, including requested filenames and executed unittest checks. Real Qwen probe generated add_numbers and a passing separate test after one implementation retry. Wider original-brief acceptance outstanding. |
| Practical persistent application | Distinct offline HTML/CSS/JS builder, browser create/edit/reload/validation checks, exact-source evidence, separate-origin preview and ZIP download | Framework checks passed using fixture source. Real model and installed application acceptance remain outstanding. |
| Interactive website | Distinct website route, browser navigation/interaction checks at desktop and mobile widths | Framework implemented; real generated website and installed acceptance remain outstanding. |
| Trustworthy completion | Software evidence binds successful execution to exact files; approval cannot manufacture evidence; generic prose cannot pass research | Focused completion/approval tests passed. Full assembled product unverified. |
| Private Windows delivery | Public Campus workflow pins private Milo source; installer upload requires explicit full-product evidence | BLOCKED by environment branch protection before any build steps. |
| Full installed acceptance | Existing Windows 12-check runner explicitly declares existing-package-regressions and full_product_accepted=false | Six required acceptance jobs and clean-install/upgrade acceptance remain outstanding. No package checksum exists. |

Focused integrated suites passed (126 checks before the final admission regressions; 78 focused checks after the mutation correction). Packaging runner: 6 tests passed. Milo renderer build passed. These overlapping suites are not additive acceptance totals.

The broad optional-integration suite was interrupted with failures and must not be described as passing. A subsequent first-failure run reached 66 passing checks and failed because its Playwright Chromium executable was missing. Downloading the required browser produced a truncated archive. Linux namespace sandbox probes also fail with Operation not permitted; software execution records the weaker timeout-subprocess-tempdir level honestly.

## Real model probe

This is an isolated Linux CPU probe, **not installed Milo acceptance**. Ollama v0.40.2 imported the publisher's Qwen3-4B-Q4_K_M GGUF under qwen3:4b. Publisher revision: `bc640142c66e1fdd12af0bd68f40445458f3869b`; downloaded GGUF SHA256: `7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5`. The official Ollama model download was blocked by the network. This imported model does not establish equivalence with the user's official Ollama chat configuration.

The recorded probe of the integrated source returned a greeting in 29.66 seconds and generated/verified the small software deliverable in 53.49 seconds, including one implementation retry. It initially copied an assertion calling main.add_numbers into main.py, causing NameError. The bounded repair produced a function returning a+b and a separate assertion main.add_numbers(2,3)==5. Execution exited 0 with empty stderr. Saved main.py SHA256: `27b1ec2958884d196da422783c6fb1905d757bbd982abf9fd714b61d0c2afd1e`; test_main.py: `caeeb846c75230f79a0841c2436b98e4daa6cff028f49146f1218237f650e0bb`.

These timings exclude download/setup, are CPU measurements in this managed environment, and are not laptop projections or memory-fit qualification. They do not qualify a larger model or simultaneous chat and job performance. Earlier probes failed; no failure was promoted to completion.

## Windows blocker and smallest unblock

[Packaging run 38173634240](https://github.com/Leehiggins111/ayven-campus/actions/runs/38173634240) has zero build steps and no assigned runner. Its failure annotation states: Branch "codex/milo-ayven-release-hardening" is not allowed to deploy to milo-package due to environment protection rules.

Allow this isolated branch in the existing milo-package environment, then rerun the pinned candidate. The authorised connector does not expose administration of environment branch protections. Do not remove the protected environment, bypass its rules, overwrite an active permitted branch, expose private artifacts, or buy runner capacity. Allowing the branch only unblocks verification; it does not complete the remaining application, website, business or installed acceptance work.

## Subsequent browser artifact candidate

Private Milo candidate: `d83c82b0f7d7cd4260fe61f3541b824ffe8316ca`. This Campus branch pins that exact candidate. Browser builds require real behavioral checks at 1280px and 390px, a JavaScript-disabled negative control, independent brief review, and source-bound persisted evidence before approval, ZIP download or preview. Fixtures verify framework behavior, not model product quality. Integrated source checks: 20 passed; Milo local focused checks: 23 passed; renderer build passed. Overlapping counts are not additive. A later real preview-wrapper check exposed blocked form submission; the sandbox now allows form events while CSP continues to forbid network form submission. Reverification passed: all six browser artifact tests, including saving and reloading through the actual separate-origin preview wrapper.

Automatic approval review blocked the broad Milo test run because it detected a possible external Ayven request. That run is not recorded as passing; verification used local focused checks. Python remains disabled in the Windows launcher pending adequate execution isolation. Browser scope is offline localStorage applications only, without accounts, network services or payments.

The first real browser-model probe timed out after 600 seconds; the second produced an invalid acceptance plan. The bounded planner repair probe also failed after 582.122 seconds, before writing any source, on the short requirement-label constraint. That constraint has since been corrected; no generated application has yet been browser-qualified. Neither was promoted to a delivered application. Windows environment protection still prevents packaging and installed acceptance. No installer or full-product acceptance is claimed.

A real-model plan was also rejected solely because its requirement label was "edit". Requirement labels now need only be nonempty; executable actions, observable assertions, category coverage and persistence checks remain mandatory. The stored real-model plan passes structural validation after that correction, and two focused planner tests passed. This is not browser execution or generated-application acceptance.
