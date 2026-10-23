# Q&T handoff: step 1 integration (22 Oct 2026)

Nothing has been merged or deployed, and no model was run. All the PRs are drafts.

## Branches and commits
| Repo | Branch | Base | Head |
|---|---|---|---|
| Leehiggins111/Milo | `integration/milo-step1-20261022` | `security/workbench-hardening-20261022` @ 3849f2d | 9e89dfc |
| Leehiggins111/Milo | `variant/milo-step1-regression-20261022` | `milo/regression-allowlist-20261022` @ 56c0fde | f425dac |
| Leehiggins111/ayven-campus | `integration/campus-step1-20261022` | `security/campus-hardening-20261022` @ c8a61e4 | (handoff-doc commit) |

The heads moved after the brief: hardening went 414d2b7 → 3849f2d and Campus went e2ee9f9 → c8a61e4. Both new tips descend from the named SHAs and already contain the product fixes. Step 1 added **tests only, with no product code changes**.

## The 7 Q&T failures
| Case | Fix (commit) | What the fix does | Reproduction before (414d2b7 / e2ee9f9) | After |
|---|---|---|---|---|
| C1-3 | d0fd987/748d32c (server.py) | Unauthenticated `/health` returns only `{"ok": bool}`. Posture moved to the authenticated `/api/health`. | `test_qa_step1_repro.test_c1_3…` FAILS: `{'ok': True, 'sandbox': 'bubblewrap', 'legacy_engines': 'disabled'}` | passes; harness body `{"ok": true}` |
| C2-5a | panel_auth `SENSITIVE_ROUTES` | `/api/settings` needs an owner session with a recent sign-in; the machine token alone gets 403 | test FAILS: `200 != 403` and settings changed | 403 "Owner sign-in required", `settings.json` unchanged |
| C2-5b | same | `/api/apply` is owner-only; the machine token is refused before the apply logic runs | test FAILS: `200 != 403` | 403, `apply_changes` not called; an owner session still reaches apply (positive control) |
| C3-0 | 748d32c `team.tests_authorised()` | The "no new tests" rule is skipped when the allow-list excludes test files | Q&T harness: job blocked | `test_batch1…test_authorised_single_file_change_passes_without_new_tests` passes; harness: awaiting_approval, machine apply 403, owner apply 200, HEAD changed |
| C12-1a/1b/2 | Campus c8a61e4 | The GPU scripts need a key of 32+ characters (exit 2 otherwise), and the workflow derives `AYVEN_ACCESS_KEY` at runtime | Q&T FAIL at e2ee9f9 | `tests/test_c12_scripts.py` 6 pass; harness `test_c1_c2_c12.py` 19 passed |

The new repro file is `workbench/src/tests/test_qa_step1_repro.py`. Running it against 414d2b7 gives 3 FAIL + 1 pass (the positive control). Against the integration head it gives 4 OK.

## Regression test syntax error and stale tests
- `test_milo_scope_regression.py` (regression head) had over-escaped `\\` line continuations and `'\\n'` literals, giving a SyntaxError on import. This is fixed on the variant branch (12/12 for the file).
- The file is ported to the integration branch with mocks adapted to the hardening API: `team._home`, `qa_evidence`, `team.workspace_patch` raising `wsgit.GitError`, and the `MAX_EXPORT` truncation. The assertions are unchanged, and it passes 12/12.
- The stale tests on the regression head (9 `test_team`, 1 `test_symlink`, 1 `test_qa_gate`) are **not fixed** on the variant. Fixing them needs the hardening changes (see `route/milo-head-comparison.md`). On the integration head the equivalent tests are current and pass.

## Suite counts (box, one suite at a time)
- Milo integration: `python -m unittest discover -s tests` → **139 OK**. `checks` 8 OK, `deploy/test_preview_proxy` 3 OK.
- Milo variant: **97 run, 1 failure, 10 errors** (the stale tests above).
- Campus integration: `pytest` → **489 passed, 13 failed, 3 skipped**. All 13 failures are environmental, because these optional extras aren't installed on the box: Playwright, crawl4ai, browser-use, mcp, pydantic-ai, qwen-agent. Affected: `test_frankenstein` ×5, `test_web_artifact` ×3, `test_v2` ×2, `test_finish` crawl ×2, `test_qa_campus_faults` web bundle ×1. qwen-agent was deliberately not installed.

## Harness drift (read before rerunning `verify-batch1`)
Running pytest C3 against the integration head gives 18 failed / 1 passed. All of these are drift in the harness's expectations, not product failures:
1. It expects machine-token apply to return 200. That contradicts the C2-5b fix. Use the `OWNER_APPLY=1` path in the copied harness at `/workspace/step1/qa/harness/run_case.py`.
2. It expects the string `out_of_scope`. Defects now read "Unauthorised change: …".
3. A forged worker result now returns HTTP 200 with `campus_rejected=true` and state failed, not 400/409/422.

Under semantic grading, every forbidden case is blocked or failed, never applied, and no files are written outside the workspace. Q&T's original harness and evidence are untouched.
