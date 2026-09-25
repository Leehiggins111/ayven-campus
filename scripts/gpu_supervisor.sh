#!/bin/bash
set -euo pipefail
llama-server -m "$HOME/models/Qwen3-32B-Q4_K_M.gguf" --host 127.0.0.1 --port 8000 --api-key "${AYVEN_LOCAL_LLM_API_KEY:-ayven-validate}" -ngl 99 -c 8192
