# Validation

## Local, no GPU

```bash
cd apps/api
PYTHONPATH=. AYVEN_LLM_STUB=1 AYVEN_RESEARCH_MODE=fixtures AYVEN_ALLOW_ESCALATION=0 pytest -q
```

This runs the API tests and the intelligence tests against fixture pages and stub models. At v1.0.0 that suite is 18 passed. It proves the loop, the ledger, the arithmetic, the audits, and the approval gate. It does **not** prove that Qwen became more intelligent.

## One command on a GPU pod

The script does not rent a machine and does not call a paid API. Run it only on a pod you already started.

```bash
git clone https://github.com/Leehiggins111/ayven-campus.git
cd ayven-campus
./scripts/run_ayven_validation.sh
```

What it does:

1. Preflight. On CUDA it discovers or downloads the Q4 GGUFs and checks llama.cpp GPU offload. It refuses to run the 32B and 30B on CPU.
2. Sets `AYVEN_ALLOW_ESCALATION=0` and `AYVEN_RESEARCH_MODE=live` when CUDA is present.
3. Runs the intelligence loop: plan, tools, claims, critic, verifier, then employee, supervisor, and manager **one model at a time**.
4. Writes `validation/runs/<timestamp>/` including `ayven.db`, `final-report.md`, `metrics.json`, and the three briefings.
5. Archives that directory to `/workspace/ayven-results/ayven-validation-<timestamp>.tar.gz` (and `$HOME/ayven-results` when that path is writable).

The log prints this before it tells you to stop:

```
========================================
COPY/SAVE RESULTS BEFORE STOPPING POD
========================================
```

Download the archive, then stop the pod. A manual copy if you need another path is printed next to the archive path:

```bash
tar -czf "$HOME/ayven-validation-results.tar.gz" -C validation/runs .
```

## Weights

Unchanged from the previous harness:

- Employee: `Qwen/Qwen3-8B` BF16
- Supervisor: `Qwen3-32B-Q4_K_M.gguf`
- Manager: `Qwen3-30B-A3B-Q4_K_M.gguf`

Sequential. B300 with CUDA 12.8 still builds llama.cpp for SM100 PTX as in `prepare.py`.

## Cost

The harness assumes `AYVEN_GPU_USD_PER_HOUR` (default 2.00) times elapsed hours. The previous sequential chat run was about $0.75 total. This run adds research fetches and more generations (about seven model calls per benchmark). A reasonable expectation on a similar pod is about **$1.50–$3.50**, dominated by how long the 32B audit generations take. The printed estimate is an assumption, not an invoice.

## Verdicts

| Verdict | Meaning |
| --- | --- |
| DRY-RUN | No CUDA, or dry-run forced. Assertions may pass on fixtures. Not a Qwen result |
| PASS | CUDA, all three roles really executed, assertions passed, no frontier call |
| FAIL | Assertions failed, a role fell back to stub on CUDA, or a generation errored |
| PARTIAL | Reserved when a non-required run records an error but was not asked to be real |

`gpu_validated` in `/health` stays false until a PASS run is reviewed. This repository does not flip that flag by itself.
