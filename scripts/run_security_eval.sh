#!/usr/bin/env bash
# Optional NVIDIA garak pass. Not part of the unit tests and not a GPU rental.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "${AYVEN_RUN_GARAK:-0}" != "1" ]]; then
  echo "GARAK OPTIONAL. Set AYVEN_RUN_GARAK=1 and a local OpenAI-compatible endpoint to run it."
  echo "This script does not start a GPU and does not call a paid API."
  exit 0
fi
if ! python3 -c "import garak" 2>/dev/null; then
  echo "garak is not installed. It stays optional. pip install garak into a separate environment if you want the probes."
  exit 0
fi
TARGET="${AYVEN_LOCAL_LLM_BASE_URL:-}"
if [[ -z "$TARGET" ]]; then
  echo "AYVEN_LOCAL_LLM_BASE_URL is empty. Refusing to send probes anywhere else."
  exit 2
fi
echo "GARAK against ${TARGET}. Paid escalation remains off."
python3 -m garak --model_type openai --model_name "${AYVEN_EMPLOYEE_MODEL:-local}" --probes promptinject,leakage,dan --generations 1
