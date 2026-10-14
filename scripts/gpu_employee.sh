#!/bin/bash
set -euo pipefail
# vLLM 0.12 removed --guided-decoding-backend. The live test does not use this 8B server.
python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen3-8B --host 127.0.0.1 --port 8000 --max-model-len 8192 --gpu-memory-utilization 0.90 --structured-outputs-config.backend xgrammar --api-key "${AYVEN_LOCAL_LLM_API_KEY:-ayven-validate}"
