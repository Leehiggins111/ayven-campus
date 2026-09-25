"""Sequential Employee → Supervisor → Manager validation."""
from __future__ import annotations
import json, os, time, sys, traceback, gc
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
    meta.update({"prompt_tokens": max(1, len(user)//4), "completion_tokens": tokens, "quantization": "stub"})
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
        kw = {"device_map": "auto"}
        try:
            self.model = AutoModelForCausalLM.from_pretrained(model_id, dtype=torch.bfloat16, **kw)
        except TypeError:
            self.model = AutoModelForCausalLM.from_pretrained(model_id, torch_dtype=torch.bfloat16, **kw)
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
        return text, {"model": self.model_id, "quantization": "bf16", "prompt_tokens": int(plen), "completion_tokens": n, "generation_s": round(dt,2), "tok_s": round(n/dt,2) if dt else 0, "peak_vram_gb": _vram()}
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
        return text, {"model": Path(self.path).name, "quantization": "gguf-q4_k_m", "prompt_tokens": int(u.get("prompt_tokens") or 0), "completion_tokens": n, "generation_s": round(dt,2), "tok_s": round(n/dt,2) if dt else 0, "peak_vram_gb": _vram()}
    def close(self):
        try:
            del self.llm
        except Exception:
            pass
        _free()

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
            page=fetch_page(url); sn=(page.get("text") or sn)[:400]; url=page.get("url") or url
        out.append({"title": h.get("title"), "url": url, "snippet": sn[:400]})
    return out

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
        return stub_complete(role, system, user)
    return session.generate(system, user)

def run(rate=0.26):
    run_dir = RUNS / _now()
    for sub in ("employee","supervisor","manager","benchmarks"):
        (run_dir/sub).mkdir(parents=True, exist_ok=True)
    weights={"employee_id": os.environ.get("AYVEN_EMPLOYEE_MODEL","Qwen/Qwen3-8B"), "supervisor_gguf": os.environ.get("AYVEN_SUPERVISOR_GGUF",""), "manager_gguf": os.environ.get("AYVEN_MANAGER_GGUF","")}
    benches={}; calls=[]; t0=time.time()
    emp_sess=open_session("EMPLOYEE", weights)
    try:
        for name, obj in OBJECTIVES.items():
            try:
                sources=research(obj[:180])
            except Exception as exc:
                sources=[{"title":"search_error","url":"","snippet":str(exc)}]
            ev="\n".join(f"{s.get('title')} {s.get('url')}\n{s.get('snippet')}" for s in sources)[:3500]
            try:
                emp, em = infer(emp_sess, "EMPLOYEE", "Ayven Employee. Facts only. Flag unknowns. Do not treat £214/£1533 as a final quote unless sizes, VAT and spec are proven.", obj+"\nSources:\n"+(ev or "none"))
            except Exception as exc:
                emp, em = f"EMPLOYEE_ERROR: {exc}\n{traceback.format_exc()[-1200:]}", {"error": str(exc)}
            em.update({"role":"EMPLOYEE","benchmark":name,"gpu":_gpu()}); calls.append(em)
            (run_dir/"employee"/f"{name}.md").write_text(emp)
            (run_dir/"employee"/f"{name}.json").write_text(json.dumps({"sources":sources,"meta":em}, indent=2, default=str))
            benches[name]={"employee":emp,"sources":sources,"source_count":sum(1 for s in sources if (s.get("url") or "").startswith("http"))}
    finally:
        if emp_sess: emp_sess.close()
    sup_sess=open_session("SUPERVISOR", weights)
    try:
        for name in OBJECTIVES:
            try:
                sup, sm = infer(sup_sess, "SUPERVISOR", "Ayven Supervisor. First line ACCEPT|RETURN|TAKE OVER|ESCALATE TO MANAGER. Catch weak quotes.", benches[name]["employee"][:4000])
            except Exception as exc:
                sup, sm = f"SUPERVISOR_ERROR: {exc}", {"error": str(exc)}
            sm.update({"role":"SUPERVISOR","benchmark":name}); calls.append(sm)
            (run_dir/"supervisor"/f"{name}.md").write_text(sup)
            decision="ACCEPT"
            for tok in ("RETURN","TAKE OVER","ESCALATE","ACCEPT"):
                if tok in sup.upper():
                    decision = "ESCALATE TO MANAGER" if tok=="ESCALATE" else tok
                    break
            benches[name]["supervisor"]=sup; benches[name]["supervisor_decision"]=decision
    finally:
        if sup_sess: sup_sess.close()
    need=[n for n,b in benches.items() if b.get("supervisor_decision")=="RETURN"]
    if need:
        emp_sess=open_session("EMPLOYEE", weights)
        try:
            for name in need:
                try:
                    emp2, em2 = infer(emp_sess, "EMPLOYEE", "Retry with supervisor feedback. Facts only.", OBJECTIVES[name]+"\nFeedback:\n"+benches[name]["supervisor"])
                except Exception as exc:
                    emp2, em2 = f"EMPLOYEE_RETRY_ERROR: {exc}", {"error": str(exc)}
                em2.update({"role":"EMPLOYEE","retry":True,"benchmark":name}); calls.append(em2)
                benches[name]["employee"]=emp2
                (run_dir/"employee"/f"{name}-retry.md").write_text(emp2)
        finally:
            if emp_sess: emp_sess.close()
    mgr_sess=open_session("MANAGER", weights)
    try:
        for name in OBJECTIVES:
            b=benches[name]
            try:
                mgr, mm = infer(mgr_sess, "MANAGER", "Ayven Manager. Do not redo trivial search. Completeness, contradictions, enquiry/approval. No frontier call.", f"Employee:\n{b['employee'][:2500]}\nSupervisor ({b.get('supervisor_decision')}):\n{(b.get('supervisor') or '')[:1500]}")
            except Exception as exc:
                mgr, mm = f"MANAGER_ERROR: {exc}", {"error": str(exc)}
            mm.update({"role":"MANAGER","benchmark":name}); calls.append(mm)
            (run_dir/"manager"/f"{name}.md").write_text(mgr)
            b["manager"]=mgr
            b["approval_needed"]=("approv" in (mgr+b["employee"]).lower() or "enquiry" in mgr.lower())
            (run_dir/"benchmarks"/f"{name}.json").write_text(json.dumps({k:b[k] for k in b if k not in ("employee","supervisor","manager","sources")}, indent=2))
    finally:
        if mgr_sess: mgr_sess.close()
    elapsed=time.time()-t0
    (run_dir/"metrics.json").write_text(json.dumps({"gpu":_gpu(),"cuda":_cuda(),"dry_run":dry_run(),"elapsed_s":round(elapsed,2),"calls":calls}, indent=2, default=str))
    lines=["# AYVEN LOCAL WORKFORCE VALIDATION", f"GPU: {_gpu()}", f"Dry run: {dry_run()}", f"Elapsed s: {round(elapsed,2)}", f"Est £: {round((elapsed/3600)*rate,3)}", "", "Employee Qwen/Qwen3-8B BF16", "Supervisor Qwen/Qwen3-32B-GGUF Q4_K_M", "Manager Qwen/Qwen3-30B-A3B-GGUF Q4_K_M", ""]
    for name,b in benches.items():
        lines += [f"## {name}", f"decision: {b.get('supervisor_decision')}", f"sources: {b.get('source_count')}", "```", (b.get("employee") or "")[:800], "```", ""]
    lines += ["VALIDATION FINISHED", "STOP THE RUNPOD POD NOW if this ran on rented GPU."]
    (run_dir/"final-report.md").write_text("\n".join(lines))
    print((run_dir/"final-report.md").read_text()[-600:])
    print("Results:", run_dir)
    return run_dir

if __name__ == "__main__":
    run()
