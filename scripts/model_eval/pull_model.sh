#!/bin/bash
# Install Ollama if needed, pull one model, and record pull time apart from the jobs.
set -euo pipefail
OUT="${AYVEN_EVAL_OUT:-eval-artifacts}"
MODEL="${AYVEN_EVAL_MODEL:?model required}"
mkdir -p "$OUT"

install_start=$(date +%s)
if ! command -v gcc >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y build-essential
fi
if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
fi
install_end=$(date +%s)

sudo systemctl stop ollama || true
export OLLAMA_NO_GPU=1
export OLLAMA_HOST=127.0.0.1:11434
if ! curl -sf http://127.0.0.1:11434/api/tags >/dev/null; then
  ollama serve > "${RUNNER_TEMP:-/tmp}/ollama.log" 2>&1 &
  ready=0
  for _ in $(seq 1 30); do
    if curl -sf http://127.0.0.1:11434/api/tags >/dev/null; then
      ready=1
      break
    fi
    sleep 2
  done
  if [ "$ready" != "1" ]; then
    echo "Ollama did not start"
    exit 1
  fi
fi

python3 scripts/model_eval/runner_info.py

avail_kb=$(df -k / | awk 'NR==2 {print $4}')
need_kb=5000000
case "$MODEL" in
  qwen3:14b|phi4) need_kb=12000000 ;;
  gemma3:12b) need_kb=10000000 ;;
  qwen3:8b) need_kb=8000000 ;;
esac
echo "disk_available_kb=$avail_kb need_kb=$need_kb"
if [ "$avail_kb" -lt "$need_kb" ]; then
  python3 -c 'import json,os; from pathlib import Path; p=Path(os.environ.get("AYVEN_EVAL_OUT","eval-artifacts")); p.mkdir(exist_ok=True); (p/"summary.json").write_text(json.dumps({"model": os.environ.get("AYVEN_EVAL_MODEL"), "status": "skipped_disk", "note": "The free runner did not have enough free disk for this model file."}, indent=2))'
  echo "skipped disk"
  exit 0
fi

pull_start=$(date +%s)
ollama pull "$MODEL"
pull_end=$(date +%s)
ollama show "$MODEL" > "$OUT/ollama-show.txt" || true
PIP_S="${PIP_S:-}" OLLAMA_INSTALL_S="$((install_end - install_start))" PULL_S="$((pull_end - pull_start))" python3 scripts/model_eval/record_setup.py
echo "pull_s=$((pull_end - pull_start))"
