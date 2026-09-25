#!/usr/bin/env bash
# One-command validation. Does not rent a GPU and does not call a paid API.
# --preflight-only installs dependencies, checks capabilities, and exits
# before any model download or llama.cpp build.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PREFLIGHT_ONLY=0
if [[ "${1:-}" == "--preflight-only" ]]; then
  PREFLIGHT_ONLY=1
fi
rm -f "$ROOT/validation/.report_ready"
echo "============================================================"
echo "Ayven one-command validation"
echo "This script does NOT rent a GPU and does NOT call a paid API."
echo "If you are on RunPod, this time is BILLABLE."
if [[ "$PREFLIGHT_ONLY" -eq 1 ]]; then
  echo "Preflight only. No model download and no exam run."
fi
echo "============================================================"

if ! python3 -m pip install -q -r "$ROOT/apps/api/requirements.txt" -r "$ROOT/apps/api/requirements-frankenstein.txt"; then
  echo "ERROR: Python requirements install failed. Preflight will record the missing capabilities."
fi
if ! python3 -m pip install -q httpx huggingface_hub transformers accelerate; then
  echo "ERROR: validation Python extras failed to install."
fi

chrome_found() {
  if [[ -n "${AYVEN_CHROME_PATH:-}" && -x "${AYVEN_CHROME_PATH}" ]]; then
    return 0
  fi
  if command -v google-chrome >/dev/null 2>&1; then
    return 0
  fi
  [[ -x /usr/local/bin/google-chrome || -x /usr/bin/google-chrome || -x /usr/bin/chromium || -x /usr/bin/chromium-browser ]]
}

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "ERROR: $* needs root, and sudo is not installed."
    return 1
  fi
}

if ! chrome_found; then
  echo "Chrome is not installed. Attempting a system Chrome install."
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "ERROR: apt-get is not available, so the system Chrome package was not installed."
  elif [[ "$(id -u)" -ne 0 ]] && ! command -v sudo >/dev/null 2>&1; then
    echo "ERROR: not root and sudo is absent. Skipping the system Chrome package."
  else
    if ! as_root apt-get update -qq; then
      echo "ERROR: apt-get update failed."
    else
      if ! as_root apt-get install -y -qq wget ca-certificates; then
        echo "ERROR: wget/ca-certificates install failed."
      fi
      tmp="$(mktemp)"
      if ! wget -q -O "$tmp" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb; then
        echo "ERROR: Chrome package download failed."
      elif ! as_root apt-get install -y -qq "$tmp"; then
        echo "ERROR: Chrome package install failed."
      fi
      rm -f "$tmp"
    fi
  fi
fi

if ! chrome_found; then
  echo "System Chrome is still missing. Falling back to Playwright Chromium."
  if ! python3 -m pip install -q playwright; then
    echo "ERROR: playwright package install failed."
  fi
  if ! python3 -m playwright install chromium; then
    echo "ERROR: playwright install chromium failed. Trying with OS dependencies."
    if ! python3 -m playwright install --with-deps chromium; then
      echo "ERROR: playwright install --with-deps chromium failed."
    fi
  fi
  found="$(python3 - << 'PY'
import os
root = os.path.expanduser("~/.cache/ms-playwright")
found = ""
if os.path.isdir(root):
    for dirpath, _dirs, files in os.walk(root):
        if "chrome" in files and "chromium" in dirpath:
            path = os.path.join(dirpath, "chrome")
            if os.access(path, os.X_OK):
                found = path
                break
print(found)
PY
)"
  if [[ -n "$found" ]]; then
    export AYVEN_CHROME_PATH="$found"
    echo "Playwright Chromium: $AYVEN_CHROME_PATH"
  else
    echo "ERROR: Playwright Chromium binary was not found after install."
  fi
fi

export PYTHONPATH="$ROOT/apps/api:${PYTHONPATH:-}"
if command -v nvidia-smi >/dev/null 2>&1 || [[ -e /dev/nvidia0 ]]; then
  export AYVEN_PREFLIGHT_GPU=1
else
  export AYVEN_PREFLIGHT_GPU=0
fi
python3 - << PY
import os, sys
root = os.environ.get("AYVEN_REPO_ROOT", "")
sys.path.insert(0, "${ROOT}/apps/api")
sys.path.insert(0, "${ROOT}")
from app.intelligence.capabilities import frankenstein_status
from validation.preflight import continue_after_preflight, gating_failures, isolation_report
caps = frankenstein_status()
print("FRANKENSTEIN PREFLIGHT")
for name, state in caps.items():
    if name != "all_core_active":
        print(f"- {name}: {state}")
print(isolation_report(str(caps.get("isolation") or "")))
gpu = os.environ.get("AYVEN_PREFLIGHT_GPU") == "1"
if caps.get("code_sandbox") != "ACTIVE":
    print("code_sandbox is REPORTED, not a gate. These exams use the calculator. The run can continue.")
if not continue_after_preflight(caps, gpu=gpu):
    print("CORE CAPABILITY INACTIVE: " + ", ".join(gating_failures(caps)))
    print("Stopping before model download. This run is not Frankenstein.")
    raise SystemExit(2)
print("PREFLIGHT OK")
PY
if [[ "$PREFLIGHT_ONLY" -eq 1 ]]; then
  echo "Preflight finished. No model was downloaded."
  exit 0
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
