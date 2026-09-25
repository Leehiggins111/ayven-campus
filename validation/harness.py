"""Sequential Employee → Supervisor → Manager validation."""
from __future__ import annotations
import json, os, time, sys, traceback, gc
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "validation" / "runs"
# Frozen exam text lives in app.intelligence.assertions so the harness and the
# unit tests cannot drift apart.

def _now():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def _cuda():
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False

def require_real():
    return os.environ.get("AYVEN_REQUIRE_REAL", "0") == "1" or (_cuda() and os.environ.get("AYVEN_VALIDATION_DRY_RUN", "0") != "1")

def dry_run():
    if require_real():
        return False
    return os.environ.get("AYVEN_VALIDATION_DRY_RUN", "0") == "1" or not _cuda()

def _gpu():
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.get_device_name(0)
    except Exception:
        pass
    return "none"

def _vram():
    try:
        import torch
        if torch.cuda.is_available():
            return round(torch.cuda.max_memory_allocated() / 1024**3, 2)
    except Exception:
        return None

def _device_of(model):
    import torch
    try:
        return next(model.parameters()).device
    except Exception:
        return torch.device("cuda" if _cuda() else "cpu")

def encode_for_generate(tok, messages, device):
    enc = tok.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, return_tensors="pt")
    input_ids = attention = None
    if hasattr(enc, "items") and not hasattr(enc, "dim"):
        data = dict(enc)
        input_ids, attention = data.get("input_ids"), data.get("attention_mask")
    else:
        input_ids = enc
    if input_ids is None:
        raise TypeError(type(enc))
    if hasattr(input_ids, "to"):
        input_ids = input_ids.to(device)
    if attention is not None and hasattr(attention, "to"):
        attention = attention.to(device)
    if getattr(input_ids, "dim", lambda: 1)() == 1:
        input_ids = input_ids.unsqueeze(0)
        if attention is not None and attention.dim() == 1:
            attention = attention.unsqueeze(0)
    kw = {"input_ids": input_ids}
    if attention is not None:
        kw["attention_mask"] = attention
    return input_ids, kw

def stub_complete(role, system, user):
    api = ROOT / "apps" / "api"
    if str(api) not in sys.path:
        sys.path.insert(0, str(api))
    from app.models import complete_role
    text, tokens, meta = complete_role(role, system, user, max_tokens=700)
    meta.update({"prompt_tokens": max(1, len(user)//4), "completion_tokens": tokens, "quantization": "stub", "execution": "stub"})
    return text, meta

def _free():
    try:
        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass

class HfSession:
    def __init__(self, model_id):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.model_id = model_id
        self.tok = AutoTokenizer.from_pretrained(model_id)
        try:
            self.model = AutoModelForCausalLM.from_pretrained(model_id, dtype=torch.bfloat16, device_map="auto")
        except TypeError:
            self.model = AutoModelForCausalLM.from_pretrained(model_id, torch_dtype=torch.bfloat16, device_map="auto")
        if self.tok.pad_token_id is None and self.tok.eos_token_id is not None:
            self.tok.pad_token = self.tok.eos_token
        self.device = _device_of(self.model)
    def generate(self, system, user):
        t0 = time.time()
        input_ids, kwargs = encode_for_generate(self.tok, [{"role":"system","content":system},{"role":"user","content":user}], self.device)
        out = self.model.generate(**kwargs, max_new_tokens=700, do_sample=False, pad_token_id=self.tok.pad_token_id or self.tok.eos_token_id)
        plen = input_ids.shape[-1]
        text = self.tok.decode(out[0][plen:], skip_special_tokens=True)
        dt = time.time()-t0
        n = max(1, int(out.shape[-1]-plen))
        return text, {"model": self.model_id, "quantization": "bf16", "execution": "real", "prompt_tokens": int(plen), "completion_tokens": n, "generation_s": round(dt,2), "tok_s": round(n/dt,2) if dt else 0, "peak_vram_gb": _vram()}
    def close(self):
        try:
            del self.model; del self.tok
        except Exception:
            pass
        _free()

class GgufSession:
    def __init__(self, path):
        from llama_cpp import Llama
        self.path = path
        self.llm = Llama(model_path=path, n_gpu_layers=-1, n_ctx=4096, verbose=False)
    def generate(self, system, user):
        t0=time.time()
        resp=self.llm.create_chat_completion(messages=[{"role":"system","content":system},{"role":"user","content":user}], max_tokens=700, temperature=0.2)
        text=resp["choices"][0]["message"]["content"]
        u=resp.get("usage") or {}
        dt=time.time()-t0
        n=int(u.get("completion_tokens") or max(1,len(text)//4))
        return text, {"model": Path(self.path).name, "quantization": "gguf-q4_k_m", "execution": "real", "prompt_tokens": int(u.get("prompt_tokens") or 0), "completion_tokens": n, "generation_s": round(dt,2), "tok_s": round(n/dt,2) if dt else 0, "peak_vram_gb": _vram(), "n_gpu_layers": -1}
    def close(self):
        try:
            del self.llm
        except Exception:
            pass
        _free()

def open_session(role, weights):
    if dry_run():
        return None
    if role=="EMPLOYEE":
        return HfSession(weights.get("employee_id", "Qwen/Qwen3-8B"))
    path = weights.get(role.lower()+"_gguf") or ""
    if path and Path(path).exists():
        return GgufSession(path)
    return None

def infer(session, role, system, user):
    if session is None:
        if require_real():
            raise RuntimeError(f"{role} real weights missing; refusing stub on CUDA validation")
        return stub_complete(role, system, user)
    text, meta = session.generate(system, user)
    meta.setdefault("execution", "real")
    return text, meta

def _export(run_dir: Path) -> Path:
    import shutil
    import tarfile
    stamp = run_dir.name
    destinations = []
    for raw in (os.environ.get("AYVEN_RESULTS_DIR"), "/workspace/ayven-results", str(Path.home() / "ayven-results")):
        if not raw:
            continue
        dest = Path(raw)
        try:
            dest.mkdir(parents=True, exist_ok=True)
            archive = dest / f"ayven-validation-{stamp}.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(run_dir, arcname=f"ayven-validation-{stamp}")
            shutil.copytree(run_dir, dest / stamp, dirs_exist_ok=True)
            destinations.append(archive)
        except Exception as exc:
            print(f"Export to {dest} failed: {exc}")
    chosen = destinations[0] if destinations else run_dir
    (ROOT / "validation" / ".last_archive_path").write_text(str(chosen))
    note = "\n".join([
        "========================================",
        "COPY/SAVE RESULTS BEFORE STOPPING POD",
        "========================================",
        f"Run directory: {run_dir}",
        f"Archive: {chosen}",
        "Automatic export already created the archive. Download it before you stop the pod.",
        "Manual copy if you need another path:",
        f"  tar -czf \"$HOME/ayven-validation-{stamp}.tar.gz\" -C \"{run_dir.parent}\" \"{run_dir.name}\"",
        "========================================",
        "",
    ])
    (run_dir / "EXPORT.txt").write_text(note)
    for archive in destinations:
        folder = archive.parent / stamp
        if folder.exists():
            (folder / "EXPORT.txt").write_text(note)
    return chosen


def run(rate=None):
    api = ROOT / "apps" / "api"
    if str(api) not in sys.path:
        sys.path.insert(0, str(api))
    hourly = float(rate if rate is not None else os.environ.get("AYVEN_GPU_USD_PER_HOUR", "2.00"))
    run_dir = RUNS / _now()
    for sub in ("employee", "supervisor", "manager", "benchmarks"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    os.environ["AYVEN_DB"] = str(run_dir / "ayven.db")
    os.environ["AYVEN_ALLOW_ESCALATION"] = "0"
    if dry_run():
        os.environ["AYVEN_RESEARCH_MODE"] = "fixtures"
        os.environ["AYVEN_LLM_STUB"] = "1"
    else:
        os.environ["AYVEN_RESEARCH_MODE"] = os.environ.get("AYVEN_RESEARCH_MODE", "live")
        os.environ["AYVEN_LLM_STUB"] = "0"
    from app.db import connect, reset_connection_state
    from app.intelligence.assertions import FOOTBALL, TRADES, VENDING, evaluate_project, failed
    from app.intelligence.execution import Programme

    reset_connection_state()
    objectives = {"A_trades": ("trades", TRADES), "B_football": ("football", FOOTBALL), "C_vending": ("vending", VENDING)}
    weights = {
        "employee_id": os.environ.get("AYVEN_EMPLOYEE_MODEL", "Qwen/Qwen3-8B"),
        "supervisor_gguf": os.environ.get("AYVEN_SUPERVISOR_GGUF", ""),
        "manager_gguf": os.environ.get("AYVEN_MANAGER_GGUF", ""),
    }
    programmes = []
    calls = []
    assertion_rows = {}
    t0 = time.time()
    conn = connect()
    for name, (_kind, objective) in objectives.items():
        project_id = f"val-{name}-{run_dir.name}"
        conn.execute(
            "INSERT INTO projects(id,title,objective,status,created_at) VALUES(?,?,?,?,?)",
            (project_id, name, objective, "running", _now()),
        )
    conn.commit()
    conn.close()
    for name, (kind, objective) in objectives.items():
        programme = Programme(f"val-{name}-{run_dir.name}", objective, None)
        programme.prepare_all()
        programmes.append((name, kind, programme))
    for role, folder in (("EMPLOYEE", "employee"), ("SUPERVISOR", "supervisor"), ("MANAGER", "manager")):
        session = open_session(role, weights)
        try:
            for name, _kind, programme in programmes:
                for prompt in programme.prompts(role):
                    try:
                        text, meta = infer(session, role, prompt["system"], prompt["user"])
                    except Exception as exc:
                        text, meta = f"{role}_ERROR: {exc}\n{traceback.format_exc()[-800:]}", {"error": str(exc), "execution": "fail", "backend": "fail"}
                    meta = dict(meta)
                    meta.update({"role": role, "benchmark": name, "gpu": _gpu()})
                    calls.append(meta)
                    programme.bind(role, prompt["id"], text, meta)
                    (run_dir / folder / f"{name}-{prompt['id'][:8]}.md").write_text(text or "")
        finally:
            if session:
                session.close()
    for name, kind, programme in programmes:
        results = evaluate_project(programme.project_id, kind)
        assertion_rows[name] = results
        (run_dir / "benchmarks" / f"{name}.json").write_text(json.dumps({"assertions": results, "failed": failed(results)}, indent=2))
        conn = connect()
        row = conn.execute("SELECT findings FROM work_packages WHERE id=?", (programme.parent_id,)).fetchone()
        conn.close()
        (run_dir / "benchmarks" / f"{name}.md").write_text(row["findings"] if row else "")
    elapsed = time.time() - t0
    real_roles = sorted({c.get("role") for c in calls if c.get("execution") == "real"})
    stub_roles = sorted({c.get("role") for c in calls if c.get("execution") == "stub" or c.get("backend") == "stub"})
    errors = [c for c in calls if c.get("error")]
    assertion_failures = {name: failed(rows) for name, rows in assertion_rows.items()}
    assertions_ok = not any(assertion_failures.values())
    if not assertions_ok:
        verdict = "FAIL"
    elif require_real() and (stub_roles or errors or not {"EMPLOYEE", "SUPERVISOR", "MANAGER"} <= set(real_roles)):
        verdict = "FAIL"
    elif errors:
        verdict = "PARTIAL"
    elif require_real():
        verdict = "PASS"
    elif not require_real():
        verdict = "DRY-RUN"
    else:
        verdict = "PARTIAL"
    est = round((elapsed / 3600) * hourly, 3)
    metrics = {
        "gpu": _gpu(),
        "cuda": _cuda(),
        "dry_run": dry_run(),
        "require_real": require_real(),
        "verdict": verdict,
        "assertions_ok": assertions_ok,
        "assertion_failures": {k: v for k, v in assertion_failures.items() if v},
        "real_roles": real_roles,
        "stub_roles": stub_roles,
        "elapsed_s": round(elapsed, 2),
        "gpu_usd_per_hour_assumption": hourly,
        "estimated_gpu_usd": est,
        "frontier_enabled": False,
        "research_mode": os.environ.get("AYVEN_RESEARCH_MODE"),
        "calls": calls,
        "note": "DRY-RUN uses fixture pages and stub models. It does not prove Qwen quality.",
    }
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    lines = [
        "# AYVEN INTELLIGENCE VALIDATION",
        f"GPU: {_gpu()}",
        f"Dry run: {dry_run()}",
        f"Research mode: {os.environ.get('AYVEN_RESEARCH_MODE')}",
        f"Elapsed s: {round(elapsed, 2)}",
        f"Estimated GPU USD at ${hourly:.2f}/hour: {est}",
        "Previous observed baseline was about $0.75 for a shorter sequential chat run. This run does more tool and audit work.",
        f"VERDICT: {verdict}",
        f"Assertions ok: {assertions_ok}",
        f"real_roles: {real_roles}",
        f"stub_roles: {stub_roles}",
        "",
        "Comparison vs v0.4 qualitative baseline (the numeric run was lost with the pod):",
        "- v0.4 replaced failed search with model memory, repeated mistakes up the hierarchy, and did not preserve labour ambiguity.",
        "- This run publishes ledger scenarios, records claims, strips think tags, and keeps escalation off.",
        "- Model intelligence is NOT proven until VERDICT is PASS on a real GPU.",
        "",
    ]
    for name, rows in assertion_rows.items():
        bad = failed(rows)
        lines.append(f"## {name}: {len(rows) - len(bad)}/{len(rows)} assertions passed")
        for item in bad:
            lines.append(f"- FAIL {item['id']}: {item['detail']}")
    lines += [
        "",
        "========================================",
        "COPY/SAVE RESULTS BEFORE STOPPING POD",
        "========================================",
    ]
    (run_dir / "final-report.md").write_text("\n".join(lines) + "\n")
    archive = _export(run_dir)
    banner = "\n".join([
        "========================================",
        "COPY/SAVE RESULTS BEFORE STOPPING POD",
        "========================================",
        f"VERDICT: {verdict}",
        f"Assertions ok: {assertions_ok}",
        f"Run directory: {run_dir}",
        f"Archive: {archive}",
        f"Report: {run_dir / 'final-report.md'}",
        f"Metrics: {run_dir / 'metrics.json'}",
        f"Estimated GPU USD: {est} at ${hourly:.2f}/hour",
        "Download the archive before you stop the pod.",
        f"Manual: tar -czf \"$HOME/ayven-validation-{run_dir.name}.tar.gz\" -C \"{run_dir.parent}\" \"{run_dir.name}\"",
        "========================================",
    ])
    print(banner)
    print((run_dir / "final-report.md").read_text()[-1200:])
    return run_dir

if __name__ == "__main__":
    run()
