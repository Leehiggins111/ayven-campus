#!/bin/bash
set -euo pipefail
key="${AYVEN_LOCAL_LLM_API_KEY:-}"
if [ "${#key}" -lt 32 ]; then echo "Refusing to start: set AYVEN_LOCAL_LLM_API_KEY (32+ characters). There is no built-in key." >&2; exit 2; fi
# vLLM 0.12 removed --guided-decoding-backend. Prefer llama.cpp for the 2507 Instruct live test.
python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen3-30B-A3B-AWQ --host 127.0.0.1 --port 8000 --max-model-len 8192 --gpu-memory-utilization 0.92 --structured-outputs-config.backend xgrammar --api-key "$key"
