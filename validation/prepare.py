"""Discover/download GGUFs and verify GPU llama.cpp."""
from __future__ import annotations
import json, os, shutil, subprocess, sys
from pathlib import Path

SUPERVISOR = {"repo": "Qwen/Qwen3-32B-GGUF", "file": "Qwen3-32B-Q4_K_M.gguf", "gb": 19.8}
MANAGER = {"repo": "Qwen/Qwen3-30B-A3B-GGUF", "file": "Qwen3-30B-A3B-Q4_K_M.gguf", "gb": 18.6}
EMPLOYEE_ID = os.environ.get("AYVEN_EMPLOYEE_MODEL", "Qwen/Qwen3-8B")
ROOT = Path(__file__).resolve().parents[1]
SEARCH_ROOTS = [Path("/workspace/models"), Path("/workspace"), Path("/models"), Path.home()/"models", Path("/root/models")]

def log(msg):
    print(msg, flush=True)

def disk_free_gb(path: Path) -> float:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(str(path)).free / (1024**3)

def find_file(name: str):
    ov = {SUPERVISOR["file"]: os.environ.get("AYVEN_SUPERVISOR_GGUF"), MANAGER["file"]: os.environ.get("AYVEN_MANAGER_GGUF")}.get(name)
    if ov and Path(ov).is_file():
        return Path(ov)
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        hit = root / name
        if hit.is_file():
            return hit
        for p in root.rglob(name):
            if p.is_file():
                return p
    return None

def download_gguf(spec, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / spec["file"]
    if target.is_file() and target.stat().st_size > 1_000_000_000:
        return target
    from huggingface_hub import hf_hub_download
    return Path(hf_hub_download(repo_id=spec["repo"], filename=spec["file"], local_dir=str(dest_dir)))

def llama_gpu_ok():
    try:
        import llama_cpp
    except Exception as exc:
        return False, f"import_fail:{exc}"
    src = Path(getattr(llama_cpp, "__file__", "") or "")
    try:
        if bool(getattr(llama_cpp, "llama_supports_gpu_offload", lambda: False)()):
            return True, "llama_supports_gpu_offload=True"
    except Exception:
        pass
    if src and any("cuda" in p.name.lower() and p.suffix==".so" for p in src.parent.glob("*")):
        return True, "cuda_shared_object_present"
    if os.environ.get("AYVEN_LLAMA_CUDA_OK") == "1":
        return True, "operator_override"
    return False, "no_gpu_offload"

def install_llama_cuda():
    env = os.environ.copy()
    env["CMAKE_ARGS"] = "-DGGML_CUDA=on"
    env["FORCE_CMAKE"] = "1"
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-cache-dir", "llama-cpp-python"], env=env)
    except subprocess.CalledProcessError as exc:
        return False, f"pip_failed:{exc.returncode}"
    return llama_gpu_ok()

def preflight():
    info = {"cuda": False, "gpu": "none", "vram_gb": None, "torch": None, "cuda_runtime": None, "disk_free_gb": None, "employee": EMPLOYEE_ID, "supervisor_path": None, "manager_path": None, "llama": None, "llama_gpu": False, "ok": False, "reason": ""}
    try:
        import torch
        info["torch"] = torch.__version__
        info["cuda"] = bool(torch.cuda.is_available())
        if info["cuda"]:
            info["gpu"] = torch.cuda.get_device_name(0)
            info["vram_gb"] = round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 2)
            info["cuda_runtime"] = getattr(torch.version, "cuda", None)
    except Exception as exc:
        info["reason"] = f"torch:{exc}"
    dest = Path(os.environ.get("AYVEN_MODEL_DIR", "/workspace/models"))
    try:
        info["disk_free_gb"] = round(disk_free_gb(dest if dest.parent.exists() else Path("/tmp")), 1)
    except Exception:
        info["disk_free_gb"] = None
    return info

def main() -> int:
    print("============================================================")
    print("BILLABLE GPU TIME if this is RunPod. Stop the pod when done.")
    print("============================================================")
    info = preflight()
    dest = Path(os.environ.get("AYVEN_MODEL_DIR", "/workspace/models"))
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except Exception:
        dest = Path.home() / "models"
        dest.mkdir(parents=True, exist_ok=True)
    sup = find_file(SUPERVISOR["file"])
    mgr = find_file(MANAGER["file"])
    missing = (0 if sup else SUPERVISOR["gb"]) + (0 if mgr else MANAGER["gb"])
    if info.get("cuda") and info.get("disk_free_gb") is not None and missing and info["disk_free_gb"] < missing + 8:
        log(f"STOP: need ~{missing+8:.0f} GB free; have {info['disk_free_gb']} GB")
        info["reason"] = "disk"
        (ROOT / "validation").mkdir(exist_ok=True)
        (ROOT / "validation" / ".last_prepare.json").write_text(json.dumps(info, indent=2))
        return 2
    if info["cuda"]:
        if sup is None:
            log(f"Downloading {SUPERVISOR['file']} -> {dest}")
            sup = download_gguf(SUPERVISOR, dest)
        if mgr is None:
            log(f"Downloading {MANAGER['file']} -> {dest}")
            mgr = download_gguf(MANAGER, dest)
        ok, why = llama_gpu_ok()
        if not ok:
            log(f"llama-cpp GPU not ready ({why}); installing CUDA build")
            ok, why = install_llama_cuda()
        info["llama"], info["llama_gpu"] = why, ok
        if not ok:
            log(f"STOP: llama.cpp has no GPU offload ({why}). Will not run 32B/30B on CPU.")
            info["reason"] = "llama_cpu_only"
            (ROOT / "validation").mkdir(exist_ok=True)
            (ROOT / "validation" / ".last_prepare.json").write_text(json.dumps(info, indent=2))
            return 3
    else:
        log("CUDA absent: dry-run only. Real hierarchy will NOT run.")
    info["supervisor_path"] = str(sup) if sup else ""
    info["manager_path"] = str(mgr) if mgr else ""
    info["ok"] = True
    (ROOT / "validation").mkdir(exist_ok=True)
    (ROOT / "validation" / ".prepared.env").write_text(
        f"export AYVEN_EMPLOYEE_MODEL={EMPLOYEE_ID}\n"
        f"export AYVEN_SUPERVISOR_GGUF={info['supervisor_path']}\n"
        f"export AYVEN_MANAGER_GGUF={info['manager_path']}\n"
        f"export AYVEN_REQUIRE_REAL={'1' if info['cuda'] else '0'}\n"
        f"export AYVEN_VALIDATION_DRY_RUN={'0' if info['cuda'] else '1'}\n"
        f"export AYVEN_LLM_STUB={'0' if info['cuda'] else '1'}\n"
    )
    (ROOT / "validation" / ".last_prepare.json").write_text(json.dumps(info, indent=2))
    log("PREFLIGHT")
    for k, v in info.items():
        log(f"  {k}: {v}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
