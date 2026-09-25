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
python3 -m pip install -q -r "$ROOT/apps/api/requirements.txt" -r "$ROOT/apps/api/requirements-frankenstein.txt" || true
python3 -m pip install -q httpx huggingface_hub transformers accelerate 2>/dev/null || true
if ! command -v google-chrome >/dev/null 2>&1 && [ ! -x /usr/local/bin/google-chrome ] && [ ! -x /usr/bin/google-chrome ]; then
  echo "Chrome is not installed. Attempting a local Chrome install for Browser Use."
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq && sudo apt-get install -y -qq wget ca-certificates || true
    tmp="$(mktemp)"
    wget -q -O "$tmp" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb || true
    sudo apt-get install -y -qq "$tmp" || true
    rm -f "$tmp"
  fi
fi
export PYTHONPATH="$ROOT/apps/api:${PYTHONPATH:-}"
if command -v nvidia-smi >/dev/null 2>&1 || [ -e /dev/nvidia0 ]; then
  python3 - << 'PY'
import os, sys
sys.path.insert(0, os.environ.get("PYTHONPATH", "").split(":")[0] or "apps/api")
from app.intelligence.capabilities import frankenstein_status
caps = frankenstein_status()
print("FRANKENSTEIN PREFLIGHT")
for name, state in caps.items():
    if name != "all_core_active":
        print(f"- {name}: {state}")
if not caps.get("all_core_active"):
    inactive = [name for name, state in caps.items() if state == "INACTIVE"]
    print("CORE CAPABILITY INACTIVE: " + ", ".join(inactive))
    print("Stopping before model download. This run is not Frankenstein.")
    raise SystemExit(2)
PY
fi
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
