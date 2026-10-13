#!/usr/bin/env bash
# Live-test plan for Qwen3-30B-A3B-Instruct-2507 on llama-server.
# Default: print the plan and exit 0. This script does not download a model,
# create a pod, or call RunPod unless AYVEN_CONFIRM_SPEND=YES.
# On a pod (RUNPOD_POD_ID set) with AYVEN_SELF_TERMINATE=1 it deletes the pod
# after the exams. Delete, not stop.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT:$ROOT/apps/api:${PYTHONPATH:-}"

python3 - << 'PY'
import json
import os
import sys
sys.path.insert(0, os.environ["PYTHONPATH"].split(":")[0])
sys.path.insert(0, os.path.join(os.environ["PYTHONPATH"].split(":")[0], "apps/api"))
from validation.live_plan import llama_server_args, plan, GGUF_FILE
body = plan()
print(json.dumps(body, indent=2))
print("LLAMA_SERVER")
print(" ".join(llama_server_args("/models/" + GGUF_FILE)))
print("load_s: not measured (no model was loaded)")
print("STOP ACTION: delete the pod. Do not stop it.")
PY

if [[ "${AYVEN_CONFIRM_SPEND:-}" != "YES" ]]; then
  echo "Plan only. No pod was created and no model was downloaded."
  echo "Refusing to create a pod. AYVEN_CONFIRM_SPEND is not YES."
  exit 0
fi

if [[ -z "${RUNPOD_POD_ID:-}" ]]; then
  echo "AYVEN_CONFIRM_SPEND=YES but this process is not on a pod."
  echo "Refusing to call the RunPod create API from this script."
  echo "Start the pod yourself, then re-run on the pod with RUNPOD_POD_ID set."
  exit 0
fi

echo "On pod ${RUNPOD_POD_ID}. Exams use the already-running llama-server."
echo "This script does not start a second model download."
# The on-pod exam entry remains scripts/run_ayven_validation.sh.
# Termination is delete. A stopped pod keeps billing the volume disk.
if [[ "${AYVEN_SELF_TERMINATE:-}" == "1" ]]; then
  echo "AYVEN lifetime or exam finished; deleting pod"
  runpodctl pod delete "$RUNPOD_POD_ID" \
    || runpodctl remove pod "$RUNPOD_POD_ID" \
    || curl --fail --request DELETE \
         --url "https://rest.runpod.io/v1/pods/$RUNPOD_POD_ID" \
         --header "Authorization: Bearer ${RUNPOD_API_KEY:-}"
fi
exit 0
