#!/bin/bash
set -euo pipefail
key="${AYVEN_LOCAL_LLM_API_KEY:-}"
if [ "${#key}" -lt 32 ]; then echo "Refusing to start: set AYVEN_LOCAL_LLM_API_KEY (32+ characters). There is no built-in key." >&2; exit 2; fi
# Grammar is per request (response_format json_schema). A server-wide grammar flag would reject prose.
llama-server -m "$HOME/models/Qwen3-32B-Q4_K_M.gguf" --host 127.0.0.1 --port 8000 --api-key "$key" -ngl 99 -c 8192
