#!/bin/bash
set -euo pipefail
python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen3-30B-A3B-AWQ --host 127.0.0.1 --port 8000 --max-model-len 8192 --gpu-memory-utilization 0.92 --api-key "${AYVEN_LOCAL_LLM_API_KEY:-ayven-validate}"
