"""Sequential Employee → Supervisor → Manager validation. Dry-run without CUDA."""
from __future__ import annotations
import json, os, time, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "validation" / "runs"
OBJECTIVES = {
    "A_trades": "Customer wants 7 internal doors supplied/fitted in Livingston. Sizes mm: 762x1981, 762x1981, 686x1981, 762x1981, 838x1981, 762x1981, 686x1981. Oak-looking, black handles. Provisional: door £82, handle £18, hinges £7, labour £95, delivery £35/job, consumables £12/door. Check if £214/door and £1,533 is a FINAL quote. Find UK suppliers, hinge positions, trade/MOQ/Scotland/VAT/measurement unknowns. Do not invent a firm quote.",
    "B_football": "Legitimate ticket/package routes for Borussia Dortmund, Ajax, Sparta Prague, Rosenborg without speculative inventory. Facts vs assumptions. Official vs reseller. Enquiry gaps. No purchases.",
    "C_vending": "UK vending-machine placement prospects. Public evidence, decision-maker type, suitability, missing info, outreach draft only. Approval before contact.",
}

def _now():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def _cuda():
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False

def dry_run():
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

def stub_complete(role, system, user):
    api = ROOT / "apps" / "api"
    if str(api) not in sys.path:
        sys.path.insert(0, str(api))
    from app.models import complete_role
    text, tokens, meta = complete_role(role, system, user, max_tokens=700)
    meta.update({"prompt_tokens": max(1, len(user)//4), "completion_tokens": tokens, "quantization": "stub"})
    return text, meta

def complete(role, system, user, weights):
    if dry_run():
        return stub_complete(role, system, user)
    if role == "EMPLOYEE":
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        mid = weights.get("employee_id", "Qwen/Qwen3-8B")
        t0 = time.time()
        tok = AutoTokenizer.from_pretrained(mid)
        model = AutoModelForCausalLM.from_pretrained(mid, torch_dtype=torch.bfloat16, device_map="auto")
        ids = tok.apply_chat_template([{"role":"system","content":system},{"role":"user","content":user}], tokenize=True, add_generation_prompt=True, return_tensors="pt").to(model.device)
        out = model.generate(ids, max_new_tokens=700, do_sample=False)
        text = tok.decode(out[0][ids.shape[-1]:], skip_special_tokens=True)
        dt = time.time()-t0
        n = max(1, out.shape[-1]-ids.shape[-1])
        meta = {"model": mid, "quantization": "bf16", "prompt_tokens": int(ids.shape[-1]), "completion_tokens": int(n), "generation_s": round(dt,2), "tok_s": round(n/dt,2) if dt else 0, "peak_vram_gb": _vram()}
        del model
        torch.cuda.empty_cache()
        return text, meta
    gguf = weights.get(role.lower()+"_gguf")
    if gguf and Path(gguf).exists():
        from llama_cpp import Llama
        t0=time.time()
        llm=Llama(model_path=gguf, n_gpu_layers=-1, n_ctx=4096, verbose=False)
        resp=llm.create_chat_completion(messages=[{"role":"system","content":system},{"role":"user","content":user}], max_tokens=700, temperature=0.2)
        text=resp["choices"][0]["message"]["content"]
        u=resp.get("usage") or {}
        dt=time.time()-t0
        n=int(u.get("completion_tokens") or max(1,len(text)//4))
        meta={"model": Path(gguf).name, "quantization": "gguf-q4_k_m", "prompt_tokens": int(u.get("prompt_tokens") or 0), "completion_tokens": n, "generation_s": round(dt,2), "tok_s": round(n/dt,2) if dt else 0, "peak_vram_gb": _vram()}
        del llm
        return text, meta
    return stub_complete(role, system, user)

def research(query):
    api = ROOT / "apps" / "api"
    if str(api) not in sys.path:
        sys.path.insert(0, str(api))
    from app.tools import combined_search, fetch_page
    hits = combined_search(query, limit=5)
    out=[]
    for h in hits:
        url=h.get("url") or ""
        sn=h.get("snippet") or ""
        if url.startswith("http") and len(sn)<80:
            page=fetch_page(url)
            sn=(page.get("text") or sn)[:400]
            url=page.get("url") or url
        out.append({"title": h.get("title"), "url": url, "snippet": sn[:400]})
    return out

def run(rate=0.26):
    run_dir = RUNS / _now()
    for sub in ("employee","supervisor","manager","benchmarks"):
        (run_dir/sub).mkdir(parents=True, exist_ok=True)
    weights={"employee_id": os.environ.get("AYVEN_EMPLOYEE_MODEL","Qwen/Qwen3-8B"), "supervisor_gguf": os.environ.get("AYVEN_SUPERVISOR_GGUF",""), "manager_gguf": os.environ.get("AYVEN_MANAGER_GGUF","")}
    benches={}
    calls=[]
    t0=time.time()
    for name, obj in OBJECTIVES.items():
        try:
            sources=research(obj[:180])
        except Exception as exc:
            sources=[{"title":"search_error","url":"","snippet":str(exc)}]
        ev="\n".join(f"{s.get('title')} {s.get('url')}\n{s.get('snippet')}" for s in sources)[:3500]
        emp, em = complete("EMPLOYEE", "Ayven Employee. Facts only. Flag unknowns. Do not treat £214/£1533 as a final quote unless sizes, VAT and spec are proven.", obj+"\nSources:\n"+(ev or "none"), weights)
        em.update({"role":"EMPLOYEE","benchmark":name,"gpu":_gpu()}); calls.append(em)
        (run_dir/"employee"/f"{name}.md").write_text(emp)
        (run_dir/"employee"/f"{name}.json").write_text(json.dumps({"sources":sources,"meta":em}, indent=2))
        sup, sm = complete("SUPERVISOR", "Ayven Supervisor. First line ACCEPT|RETURN|TAKE OVER|ESCALATE TO MANAGER. Catch weak quotes.", emp[:4000], weights)
        sm.update({"role":"SUPERVISOR","benchmark":name}); calls.append(sm)
        (run_dir/"supervisor"/f"{name}.md").write_text(sup)
        decision="ACCEPT"
        for tok in ("RETURN","TAKE OVER","ESCALATE","ACCEPT"):
            if tok in sup.upper():
                decision = "ESCALATE TO MANAGER" if tok=="ESCALATE" else tok
                break
        if decision=="RETURN":
            emp2, em2 = complete("EMPLOYEE", "Retry with supervisor feedback. Facts only.", obj+"\nFeedback:\n"+sup, weights)
            em2.update({"role":"EMPLOYEE","retry":True,"benchmark":name}); calls.append(em2)
            emp=emp2
            (run_dir/"employee"/f"{name}-retry.md").write_text(emp2)
        mgr, mm = complete("MANAGER", "Ayven Manager. Do not redo trivial search. Completeness, contradictions, enquiry/approval. No frontier call.", f"Employee:\n{emp[:2500]}\nSupervisor ({decision}):\n{sup[:1500]}", weights)
        mm.update({"role":"MANAGER","benchmark":name}); calls.append(mm)
        (run_dir/"manager"/f"{name}.md").write_text(mgr)
        benches[name]={"employee":emp,"supervisor":sup,"manager":mgr,"supervisor_decision":decision,"source_count":sum(1 for s in sources if (s.get("url") or "").startswith("http")),"approval_needed":("approv" in (mgr+emp).lower() or "enquiry" in mgr.lower())}
        (run_dir/"benchmarks"/f"{name}.json").write_text(json.dumps({k:benches[name][k] for k in benches[name] if k not in ("employee","supervisor","manager")}, indent=2))
    elapsed=time.time()-t0
    (run_dir/"metrics.json").write_text(json.dumps({"gpu":_gpu(),"cuda":_cuda(),"dry_run":dry_run(),"elapsed_s":round(elapsed,2),"calls":calls}, indent=2))
    lines=["# AYVEN LOCAL WORKFORCE VALIDATION", f"GPU: {_gpu()}", f"Dry run: {dry_run()}", f"Elapsed s: {round(elapsed,2)}", f"Est £: {round((elapsed/3600)*0.26,3)}", "", "Employee Qwen/Qwen3-8B BF16", "Supervisor Qwen/Qwen3-32B-GGUF Q4_K_M", "Manager Qwen/Qwen3-30B-A3B-GGUF Q4_K_M", ""]
    for name,b in benches.items():
        lines += [f"## {name}", f"decision: {b['supervisor_decision']}", f"sources: {b['source_count']}", "```", (b["employee"] or "")[:800], "```", ""]
    lines += ["VALIDATION FINISHED", "STOP THE RUNPOD POD NOW if this ran on rented GPU."]
    (run_dir/"final-report.md").write_text("\n".join(lines))
    print((run_dir/"final-report.md").read_text()[-500:])
    print("Results:", run_dir)
    return run_dir

if __name__ == "__main__":
    run()
