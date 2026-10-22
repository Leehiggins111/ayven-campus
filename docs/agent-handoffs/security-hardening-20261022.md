# Security hardening — Campus side (22 Oct 2026)

Branch `security/campus-hardening-20261022`, based on `push/campus-workbench-20261019` @ `a40c47b`.
**Not merged, not deployed, not independently verified.**

The full handoff (environment inventory, evidence inventories, owner decisions, QA instructions) is in the Milo repository, branch `security/workbench-hardening-20261022`, at `workbench/docs/agent-handoffs/security-hardening-20261022.md`. This branch must ship together with that one.

| Commit | Fix |
| --- | --- |
| `05a61d9` | C12: a missing access key fails closed outside an explicitly declared local/test/dev run; MCP actions are checked with their real approval state |
| `4e7efa2` | C3: orders require an exact `allowed_files` list; Campus re-checks every PASS against the patch's own headers and stores out-of-scope or unverifiable work as FAIL; acceptance requires `scope_verified` (results recorded before this change cannot be accepted and are not modified); C12: `execution_mode: NONE` is valid only for non-PASS results |

**API change for callers (Milo / integration):** `POST /milo/executions` now **requires** `allowed_files: ["exact/relative/file.py", ...]`. A PASS result must include a `changes.diff` artifact. Execution status gains `scope_verified`, plus `worker_verdict` and `campus_rejected` when Campus overrides a worker PASS.

**Tests (Linux):**
- `tests/test_workbench_scope_http.py`: 12 tests; 11 fail on `a40c47b`.
- Bridge and scope files together: 28/28.
- Full suite: 498 passed vs 486 on base, with an identical set of 19 environment failures and 1 collection error.

**Open owner decision:** how build orders treat tests when no test file is authorised (see the full handoff, section 6.2).
