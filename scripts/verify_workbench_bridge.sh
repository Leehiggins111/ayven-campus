#!/usr/bin/env bash
set -euo pipefail
# Run in an authorized Campus checkout; no deploy, merge, or external writes.
cd "$(dirname "$0")/.."
export AYVEN_ENV=local
export AYVEN_LLM_STUB=1
export AYVEN_THREAD_STUB=1
export AYVEN_DB="$(mktemp -d)/bridge-test.sqlite"
export AYVEN_API_TOKEN="${AYVEN_API_TOKEN:-$(python -c 'import secrets;print(secrets.token_hex(24))')}"
export AYVEN_WORKER_TOKEN="${AYVEN_WORKER_TOKEN:-$(python -c 'import secrets;print(secrets.token_hex(24))')}"
python -m pytest -q tests/test_workbench_bridge_http.py
echo "Campus HTTP bridge tests completed; live Qwen and full process restart NOT covered."
