# Automated Qwen validation

On an already-running GPU pod:

```
git clone https://github.com/Leehiggins111/ayven-campus.git
cd ayven-campus
./scripts/run_ayven_validation.sh
```

Employee: Qwen/Qwen3-8B BF16 (~16GB VRAM, proven 15.38 GiB).
Supervisor: Qwen/Qwen3-32B-GGUF Q4_K_M (19.8GB disk, ~24GB VRAM). 32B BF16 will not fit 30.5 GiB.
Manager: Qwen/Qwen3-30B-A3B-GGUF Q4_K_M (18.6GB disk, ~20GB VRAM).

Sequential. Stop the pod when the banner prints. Do not leave GPU idle.
