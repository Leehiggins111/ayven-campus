#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "Ayven validation harness — will not rent a GPU"
python3 -m pip install -q httpx 2>/dev/null || true
if python3 -c "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
  echo CUDA:yes
  export AYVEN_VALIDATION_DRY_RUN=0 AYVEN_LLM_STUB=0
  python3 -m pip install -q transformers accelerate huggingface_hub 2>/dev/null || true
else
  echo "CUDA:no DRY RUN"
  export AYVEN_VALIDATION_DRY_RUN=1 AYVEN_LLM_STUB=1
fi
export PYTHONPATH="$ROOT/apps/api:${PYTHONPATH:-}"
python3 "$ROOT/validation/harness.py"
echo
echo "VALIDATION FINISHED — STOP THE RUNPOD POD NOW IF THIS WAS A PAID GPU"
