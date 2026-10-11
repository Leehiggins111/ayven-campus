"""Pull nothing. Run the requests already selected by the workflow, and record the machine."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from scripts.model_eval.objectives import CANDIDATES, REQUESTS
from scripts.model_eval.score import format_timing_table, lee_job_estimate, project_to_lee, two_model_fit


def _get(url: str, body: dict | None = None, timeout: float = 30) -> dict:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def model_file_bytes(base: str, tag: str) -> int:
    payload = _get(f"{base}/api/tags", timeout=30)
    for item in payload.get("models") or []:
        name = item.get("name") or ""
        if name == tag or name.split(":")[0] == tag or name.startswith(tag + ":") or tag.startswith(name):
            if name == tag or name == tag + ":latest" or tag == name:
                return int(item.get("size") or 0)
    for item in payload.get("models") or []:
        name = item.get("name") or ""
        if tag.split(":")[0] in name:
            return int(item.get("size") or 0)
    return 0


def probe(base: str, tag: str) -> dict:
    """A short generation so a model that cannot finish three jobs still has a token rate."""
    chat = "qwen" not in tag.lower()
    started = time.time()
    if chat:
        payload = _get(
            f"{base}/api/chat",
            {
                "model": tag,
                "messages": [{"role": "user", "content": "Reply with one sentence about a kettle."}],
                "stream": False,
                "think": False,
                "options": {"num_predict": 64, "num_ctx": 2048, "temperature": 0.2},
            },
            timeout=float(os.environ.get("AYVEN_LLM_TIMEOUT_S", "900")),
        )
    else:
        payload = _get(
            f"{base}/api/generate",
            {
                "model": tag,
                "prompt": "Reply with one sentence about a kettle.",
                "stream": False,
                "think": False,
                "options": {"num_predict": 64, "num_ctx": 2048, "temperature": 0.2},
            },
            timeout=float(os.environ.get("AYVEN_LLM_TIMEOUT_S", "900")),
        )
    wall = time.time() - started
    tokens = int(payload.get("eval_count") or 0)
    duration = payload.get("eval_duration")
    eval_s = float(duration) / 1_000_000_000 if isinstance(duration, (int, float)) and duration > 0 else 0
    prompt_n = int(payload.get("prompt_eval_count") or 0)
    prompt_raw = payload.get("prompt_eval_duration")
    prompt_s = float(prompt_raw) / 1_000_000_000 if isinstance(prompt_raw, (int, float)) and prompt_raw > 0 else 0
    load_raw = payload.get("load_duration")
    load_s = float(load_raw) / 1_000_000_000 if isinstance(load_raw, (int, float)) and load_raw > 0 else 0
    rate = tokens / eval_s if eval_s else (tokens / wall if wall else 0)
    loaded = {}
    try:
        loaded = _get(f"{base}/api/ps", timeout=15)
    except Exception as exc:
        loaded = {"error": type(exc).__name__}
    return {
        "probe_tokens": tokens,
        "probe_wall_s": round(wall, 2),
        "probe_tokens_per_second": round(rate, 2),
        "probe_prompt_tokens_per_second": round(prompt_n / prompt_s, 2) if prompt_s else 0,
        "probe_load_s": round(load_s, 3),
        "loaded": loaded,
    }


def sample_loaded(base: str) -> dict:
    try:
        payload = _get(f"{base}/api/ps", timeout=10)
    except Exception:
        return {"vram_bytes": 0, "size_bytes": 0}
    vram = size = 0
    for item in payload.get("models") or []:
        vram = max(vram, int(item.get("size_vram") or 0))
        size = max(size, int(item.get("size") or 0))
    return {"vram_bytes": vram, "size_bytes": size}


def sample_memory() -> dict:
    available = total = ollama = 0
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if line.startswith("MemAvailable:") and len(parts) >= 2:
                available = int(parts[1])
            elif line.startswith("MemTotal:") and len(parts) >= 2:
                total = int(parts[1])
    except OSError:
        pass
    proc = Path("/proc")
    if proc.exists():
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                comm = (entry / "comm").read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if "ollama" not in comm:
                continue
            try:
                for status in (entry / "status").read_text(encoding="utf-8").splitlines():
                    if status.startswith("VmRSS:"):
                        ollama += int(status.split()[1])
            except OSError:
                continue
    vram = ""
    try:
        vram = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            text=True,
            timeout=10,
        ).strip()
    except Exception:
        vram = "none"
    return {"mem_total_kb": total, "mem_available_kb": available, "ollama_rss_kb": ollama, "vram": vram}


def _run_child(env: dict, out: Path, budget: float, base: str) -> dict:
    peak = sample_memory()
    peak["vram_bytes"] = 0
    peak["size_bytes"] = 0
    cmd = [sys.executable, str(Path(__file__).with_name("request.py"))]
    started = time.time()
    proc = subprocess.Popen(cmd, env=env)
    while proc.poll() is None:
        now = sample_memory()
        loaded = sample_loaded(base)
        if now["ollama_rss_kb"] > peak["ollama_rss_kb"]:
            peak["ollama_rss_kb"] = now["ollama_rss_kb"]
        if now["mem_available_kb"] and (not peak["mem_available_kb"] or now["mem_available_kb"] < peak["mem_available_kb"]):
            peak["mem_available_kb"] = now["mem_available_kb"]
            peak["mem_total_kb"] = now["mem_total_kb"]
        peak["vram_bytes"] = max(peak["vram_bytes"], loaded["vram_bytes"])
        peak["size_bytes"] = max(peak["size_bytes"], loaded["size_bytes"])
        if time.time() - started > budget:
            proc.kill()
            proc.wait(timeout=30)
            return {"status": "timeout", "wall_s": round(time.time() - started, 1), "memory": peak, "timeouts": 1}
        time.sleep(5)
    result_path = out / "result.json"
    if proc.returncode != 0 or not result_path.exists():
        return {"status": "failed", "exit_code": proc.returncode, "wall_s": round(time.time() - started, 1), "memory": peak}
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["memory"] = peak
    return result


def _selected() -> list[dict]:
    raw = (os.environ.get("AYVEN_EVAL_REQUESTS") or "").strip()
    if not raw:
        return list(REQUESTS)
    wanted = {item.strip() for item in raw.split(",") if item.strip()}
    return [item for item in REQUESTS if item["id"] in wanted]


def _timing_row(tag: str, job: dict, pull_s: float, probe_load_s: float, file_bytes: int, copy_gbps: float) -> dict:
    timing = job.get("timing") or {}
    memory = job.get("memory") or {}
    wall = float(job.get("wall_s") or 0)
    in_job_load = float(timing.get("load_s") or 0)
    job_s = max(0.0, wall - in_job_load)
    prompt_s = float(timing.get("prompt_eval_s") or 0)
    eval_s = float(timing.get("eval_s") or 0)
    unscaled = max(0.0, job_s - prompt_s - eval_s)
    projected = project_to_lee(
        file_bytes,
        int(timing.get("prompt_tokens") or 0),
        int(timing.get("completion_tokens") or 0),
        prompt_s,
        eval_s,
        copy_gbps,
        unscaled,
    )
    timeouts = int(timing.get("timeouts") or 0) + int(job.get("timeouts") or 0)
    if job.get("status") == "timeout":
        timeouts += 1
    ram_kb = int(memory.get("ollama_rss_kb") or 0)
    vram = int(memory.get("vram_bytes") or 0)
    lee = projected.get("lee_gpu_job_minutes")
    return {
        "model": tag,
        "request": job.get("id") or "",
        "pull": f"{pull_s / 60:.1f} min" if pull_s else "",
        "load": f"{(probe_load_s + in_job_load):.1f} s",
        "job": f"{job_s / 60:.1f} min",
        "prompt_tps": timing.get("prompt_tokens_per_second") or "",
        "gen_tps": timing.get("generation_tokens_per_second") or "",
        "calls": timing.get("model_calls"),
        "retries": timing.get("retries"),
        "timeouts": timeouts,
        "ram": f"{ram_kb / 1024 / 1024:.1f} GB" if ram_kb else "",
        "vram": f"{vram / 1e9:.2f} GB" if vram else "none",
        "lee_job": f"{lee} min" if lee else "",
        "projection": projected,
        "job_wall_s": wall,
        "job_s": round(job_s, 1),
    }


def _write_table(root: Path, rows: list[dict]) -> None:
    table = format_timing_table(rows)
    (root / "timing.md").write_text(table, encoding="utf-8")
    (root / "timing.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")


def main() -> int:
    tag = os.environ.get("AYVEN_EVAL_MODEL", "").strip()
    if not tag:
        print("AYVEN_EVAL_MODEL is empty", flush=True)
        return 2
    root = Path(os.environ.get("AYVEN_EVAL_OUT", "eval-artifacts"))
    root.mkdir(parents=True, exist_ok=True)
    base = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
    if not base.startswith("http"):
        base = "http://" + base
    file_bytes = model_file_bytes(base, tag)
    catalogue = next((item for item in CANDIDATES if item["tag"] == tag), {})
    try:
        probed = probe(base, tag)
    except Exception as exc:
        summary = {
            "model": tag,
            "status": "probe_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "file_bytes": file_bytes,
            "licence": catalogue.get("licence", ""),
            "restrictions": catalogue.get("restrictions", ""),
        }
        (root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"eval {tag} status=probe_failed", flush=True)
        return 0
    setup = {}
    runner = {}
    setup_path = root / "setup.json"
    runner_path = root / "runner.json"
    if setup_path.exists():
        setup = json.loads(setup_path.read_text(encoding="utf-8"))
    if runner_path.exists():
        runner = json.loads(runner_path.read_text(encoding="utf-8"))
    pull_s = float(setup.get("pull_s") or 0)
    copy_gbps = float(runner.get("copy_gbps") or 0)
    budget = float(os.environ.get("AYVEN_EVAL_JOB_BUDGET_S", "5400"))
    jobs = []
    rows = []
    if (root / "timing.json").exists():
        try:
            rows = json.loads((root / "timing.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rows = []
    probe_load = float(probed.get("probe_load_s") or 0)
    selected = [] if os.environ.get("AYVEN_EVAL_COMPARE_ONLY", "0") == "1" else _selected()
    for request in selected:
        out = root / request["id"]
        env = os.environ.copy()
        env["AYVEN_EVAL_MODE"] = "pipeline"
        env["AYVEN_EVAL_REQUEST"] = request["id"]
        env["AYVEN_EVAL_OUT"] = str(out)
        env["AYVEN_DB"] = str(out / "campus.db")
        env["AYVEN_EVIDENCE_PACK"] = "full"
        env["AYVEN_EVAL_CALL_LOG"] = str(out / "calls.jsonl")
        env.setdefault("PYTHONPATH", os.getcwd() + os.pathsep + str(Path(os.getcwd()) / "apps" / "api"))
        print(f"eval start {request['id']}", flush=True)
        job = _run_child(env, out, budget, base)
        if job.get("status") == "completed":
            job["id"] = request["id"]
            job["timing"] = job.get("timing") or {}
        else:
            job["id"] = request["id"]
        # The child writes timing inside result.json. Prefer that over the parent copy.
        result_path = out / "result.json"
        if result_path.exists():
            saved = json.loads(result_path.read_text(encoding="utf-8"))
            saved["memory"] = job.get("memory")
            saved["status"] = saved.get("status") or job.get("status")
            job = saved
        jobs.append(job)
        row = _timing_row(tag, job, pull_s, probe_load, file_bytes, copy_gbps)
        rows = [item for item in rows if not (item.get("model") == tag and item.get("request") == request["id"])]
        rows.append(row)
        _write_table(root, rows)
        print(
            f"timing {tag} {request['id']} pull_s={pull_s:.0f} load_s={probe_load + float((job.get('timing') or {}).get('load_s') or 0):.1f} "
            f"job_s={row['job_s']} prompt_tps={row['prompt_tps']} gen_tps={row['gen_tps']}",
            flush=True,
        )
    compact = None
    if os.environ.get("AYVEN_EVAL_COMPARE", "0") == "1" and any(item["id"] == "alba" for item in _selected()):
        evidence = root / "alba" / "evidence.json"
        out = root / "alba-compact"
        if evidence.exists():
            env = os.environ.copy()
            env["AYVEN_EVAL_MODE"] = "compact"
            env["AYVEN_EVAL_EVIDENCE"] = str(evidence)
            env["AYVEN_EVAL_OUT"] = str(out)
            env["AYVEN_DB"] = str(out / "campus.db")
            env["AYVEN_EVIDENCE_PACK"] = "compact"
            print("eval start alba-compact", flush=True)
            compact = _run_child(env, out, budget, base)
        else:
            compact = {"status": "skipped", "reason": "Alba produced no evidence file."}
    completion = sum(int(item.get("completion_tokens") or 0) for item in jobs if item.get("status") == "completed")
    wall = sum(float(item.get("wall_s") or 0) for item in jobs)
    estimate = lee_job_estimate(file_bytes, completion, wall)
    chat = next((item for item in CANDIDATES if item["tag"] == "qwen3:4b"), {})
    chat_bytes = int(os.environ.get("AYVEN_EVAL_CHAT_BYTES") or 0)
    if tag == "qwen3:4b":
        chat_bytes = file_bytes
    fit = two_model_fit(chat_bytes or int(chat.get("listed_gb", 2.5) * 1_000_000_000), file_bytes or int(catalogue.get("listed_gb", 0) * 1_000_000_000))
    summary = {
        "model": tag,
        "file_bytes": file_bytes,
        "listed_gb": catalogue.get("listed_gb"),
        "licence": catalogue.get("licence", ""),
        "restrictions": catalogue.get("restrictions", ""),
        "source": catalogue.get("source", ""),
        "probe": probed,
        "jobs": [{key: job.get(key) for key in ("id", "status", "wall_s", "timing", "score", "completion_tokens", "memory", "exit_code", "reason")} for job in jobs],
        "compact": compact,
        "lee_estimate": estimate,
        "two_model_fit": fit,
        "runner": runner,
        "setup": {
            "pull_s": pull_s,
            "probe_load_s": probe_load,
            "pip_s": setup.get("pip_s"),
            "ollama_install_s": setup.get("ollama_install_s"),
        },
        "timing_rows": rows,
        "not_install_default": True,
    }
    (root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(
        f"eval {tag} file_bytes={file_bytes} probe_tps={probed.get('probe_tokens_per_second')} jobs={len(jobs)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
