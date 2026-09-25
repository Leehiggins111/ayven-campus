#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
rm -f "$ROOT/validation/.report_ready"
echo "============================================================"
echo "Ayven one-command validation"
echo "This script does NOT rent a GPU and does NOT call a paid API."
echo "If you are on RunPod, this time is BILLABLE."
echo "============================================================"
python3 -m pip install -q httpx huggingface_hub transformers accelerate 2>/dev/null || true
python3 "$ROOT/validation/prepare.py"
status=$?
if [ "$status" -ne 0 ]; then
  echo "PREPARE FAILED ($status). Not starting incomplete real inference."
  echo "No results archive was created, so this script is not telling you to stop the pod."
  exit "$status"
fi
if [ -f "$ROOT/validation/.prepared.env" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/validation/.prepared.env"
fi
export PYTHONPATH="$ROOT/apps/api:${PYTHONPATH:-}"
export AYVEN_ALLOW_ESCALATION=0
set +e
python3 "$ROOT/validation/harness.py"
status=$?
set -e
if [ ! -f "$ROOT/validation/.report_ready" ]; then
  echo "No finalized report was written. No STOP POD instruction is printed."
  exit "$status"
fi
echo
echo "============================================================"
echo "VALIDATION FINISHED"
echo "The archive path, the copy command, and the full report were printed above."
echo "STOP THE RUNPOD POD NOW if this was a paid GPU."
echo "Do not leave billed GPU time idle."
echo "============================================================"
exit "$status"
