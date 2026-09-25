#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
banner() {
  echo
  echo "============================================================"
  echo "VALIDATION FINISHED OR STOPPED"
  echo "STOP THE RUNPOD POD NOW if this was a paid GPU."
  echo "Do not leave billed GPU time idle."
  echo "============================================================"
}
trap banner EXIT
echo "============================================================"
echo "Ayven one-command validation"
echo "This script does NOT rent a GPU."
echo "If you are on RunPod, this time is BILLABLE."
echo "============================================================"
python3 -m pip install -q httpx huggingface_hub transformers accelerate 2>/dev/null || true
python3 "$ROOT/validation/prepare.py"
status=$?
if [ "$status" -ne 0 ]; then
  echo "PREPARE FAILED ($status). Not starting incomplete real inference."
  exit "$status"
fi
if [ -f "$ROOT/validation/.prepared.env" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/validation/.prepared.env"
fi
export PYTHONPATH="$ROOT/apps/api:${PYTHONPATH:-}"
python3 "$ROOT/validation/harness.py"
