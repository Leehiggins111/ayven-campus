"""Score a model-written plan. The gate is the product gate. This file does not change it."""

from __future__ import annotations

import re

_SELF_TALK = (
    "let's",
    "i think",
    "we need to",
    "the user",
    "they need me",
    "let me",
    "i need to",
    "service words",
    "kind of buyer",
)
_COPY = (
    "name a customer",
    "name the service",
    "name the problem",
    "ask the customer to take a step",
    "does not ask the customer",
    "cost guide",
    "the previous answer was rejected",
)


def _money(text: str) -> list[str]:
    found = re.findall(r"£\s*[\d,]+(?:\.\d+)?", text or "")
    return [re.sub(r"\s+", "", item) for item in found]


def score_plan(plan: str, objective: str, evidence: str, attempts: list[dict] | None = None) -> dict:
    """Section-gate results, invented prices, and how often replies talk about the task."""
    from app.intelligence.deliverable import parse_sections, plan_sections_filled, section_rejection

    sections = parse_sections(plan or "")
    reasons = {}
    for key, body in sections.items():
        reason = section_rejection(key, body or "", evidence or "", objective or "")
        if reason:
            reasons[key] = reason
    attempts = attempts or []
    section_attempts = [item for item in attempts if str(item.get("kind") or "").startswith("section:")]
    self_talk = 0
    copied = 0
    for item in section_attempts:
        blob = f"{item.get('response') or ''}\n{item.get('reason') or ''}".lower()
        if any(phrase in blob for phrase in _SELF_TALK):
            self_talk += 1
        if any(phrase in blob for phrase in _COPY):
            copied += 1
    total = len(section_attempts)
    plan_money = _money(plan or "")
    evidence_money = set(_money(evidence or ""))
    invented = [item for item in plan_money if item not in evidence_money]
    opened = []
    for line in (evidence or "").splitlines():
        if line.strip().startswith("http"):
            opened.append(line.strip())
    cited = 0
    lowered = (plan or "").lower()
    for url in opened:
        host = url.split("/")[2] if url.startswith("http") and len(url.split("/")) > 2 else ""
        if url.lower() in lowered or (host and host.lower() in lowered):
            cited += 1
    prompt_tokens = [int(item.get("prompt_tokens") or 0) for item in attempts if int(item.get("prompt_tokens") or 0)]
    contexts = [int(item.get("num_ctx") or 0) for item in attempts if int(item.get("num_ctx") or 0)]
    max_prompt = max(prompt_tokens) if prompt_tokens else 0
    num_ctx = max(contexts) if contexts else 0
    return {
        "gate_pass": bool(plan_sections_filled(plan or "", evidence or "", objective or "")),
        "section_reasons": reasons,
        "sections_present": sorted(key for key, body in sections.items() if (body or "").strip()),
        "self_talk_replies": self_talk,
        "copied_instruction_replies": copied,
        "section_attempts": total,
        "self_talk_rate": round(self_talk / total, 3) if total else 0,
        "copy_rate": round(copied / total, 3) if total else 0,
        "prices_in_plan": plan_money,
        "invented_prices": invented,
        "opened_pages": len(opened),
        "pages_cited": cited,
        "max_prompt_tokens": max_prompt,
        "num_ctx": num_ctx,
        "prompt_fills_context": bool(num_ctx and max_prompt >= num_ctx - 128),
    }


def lee_job_estimate(file_bytes: int, completion_tokens: int, cpu_wall_s: float) -> dict:
    """Extrapolate this laptop class from measured tokens. It is not a run on that machine."""
    file_gb = round((file_bytes or 0) / 1_000_000_000, 2)
    tokens = max(int(completion_tokens or 0), 0)
    estimate = {
        "machine": "15.7 GB RAM, Intel i5-11400H (6 cores), NVIDIA RTX 2050 4 GB plus Intel UHD",
        "file_gb": file_gb,
        "completion_tokens": tokens,
        "cpu_wall_minutes": round((cpu_wall_s or 0) / 60, 1),
        "measured_on_rtx_2050": False,
    }
    if file_gb <= 0 or tokens <= 0:
        estimate["band"] = "not_measured"
        estimate["note"] = "No completed generation, so there is no token count to scale."
        return estimate
    if file_gb <= 3.6:
        estimate["band"] = "weights_fit_in_4gb"
        estimate["note"] = (
            "The file fits in 4 GB of VRAM, so the CPU-runner time is a ceiling, not the expected time. "
            "This eval did not measure the RTX 2050."
        )
        return estimate
    if file_gb <= 10:
        estimate["band"] = "partial_offload"
        estimate["minutes_at_2_tokens_per_second"] = round(tokens / 2 / 60, 1)
        estimate["minutes_at_5_tokens_per_second"] = round(tokens / 5 / 60, 1)
        estimate["note"] = (
            "The file is larger than 4 GB of VRAM, so part of the model stays in system RAM. "
            "The range applies 2 and 5 tokens per second, a few tokens per second on this laptop class, "
            "to the token count this job produced. It is not a measurement on the RTX 2050."
        )
        return estimate
    estimate["band"] = "above_install_cap"
    estimate["note"] = "The file is above the 9-10 GB install cap, so it is not a candidate for the default."
    return estimate


def _timeout(call: dict) -> bool:
    reason = f"{call.get('finish_reason') or ''} {call.get('error') or ''}".lower()
    if "timeout" in reason or "timed out" in reason:
        return True
    return False


def call_timing(calls: list[dict], section_attempts: list[dict] | None = None) -> dict:
    """Speeds from Ollama's own clocks. Prompt eval and generation stay separate."""
    prompt_tokens = sum(int(item.get("prompt_tokens") or 0) for item in calls)
    completion_tokens = sum(int(item.get("completion_tokens") or 0) for item in calls)
    prompt_s = sum(float(item.get("prompt_eval_s") or 0) for item in calls)
    eval_s = sum(float(item.get("eval_s") or 0) for item in calls)
    load_s = sum(float(item.get("load_s") or 0) for item in calls)
    retries = 0
    for item in section_attempts or []:
        if int(item.get("attempt") or 0) > 1:
            retries += 1
    return {
        "model_calls": len(calls),
        "retries": retries,
        "timeouts": sum(1 for item in calls if _timeout(item)),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "prompt_eval_s": round(prompt_s, 3),
        "eval_s": round(eval_s, 3),
        "load_s": round(load_s, 3),
        "prompt_tokens_per_second": round(prompt_tokens / prompt_s, 2) if prompt_s else 0,
        "generation_tokens_per_second": round(completion_tokens / eval_s, 2) if eval_s else 0,
    }


# Datasheet figures, not measurements on Lee's PC.
LEE_CPU_BANDWIDTH_GBPS = 51.2  # i5-11400H, dual-channel DDR4-3200
LEE_GPU_BANDWIDTH_GBPS = 112.0  # RTX 2050 laptop, 64-bit GDDR6 at 14 GT/s
LEE_VRAM_BYTES = 4 * 1000 ** 3
LEE_GPU_WEIGHT_BUDGET = int(3.2 * 1000 ** 3)


def project_to_lee(
    file_bytes: int,
    prompt_tokens: int,
    completion_tokens: int,
    prompt_eval_s: float,
    eval_s: float,
    runner_copy_gbps: float,
    unscaled_s: float,
) -> dict:
    """Project job compute from the runner's measured token rate.

    Achieved read rate is generation tokens/s times the model file size, which is
    what a dense model reads per generated token. The runner copy rate is a memcpy
    measured on that same machine. Their ratio is the fraction of measured copy
    bandwidth the generate loop actually used. That fraction is applied to the
    i5-11400H dual-channel DDR4-3200 datasheet rate (51.2 GB/s) and, when the
    weights fit, to the RTX 2050 datasheet rate (112 GB/s). Web research and
    other non-model time is added back unchanged.
    """
    file_bytes = int(file_bytes or 0)
    gen_tps = (completion_tokens / eval_s) if eval_s else 0
    prompt_tps = (prompt_tokens / prompt_eval_s) if prompt_eval_s else 0
    achieved_gbps = (gen_tps * file_bytes / 1e9) if file_bytes and gen_tps else 0
    copy = float(runner_copy_gbps or 0)
    efficiency = min(1.0, achieved_gbps / copy) if copy and achieved_gbps else 0
    out = {
        "measured_generation_tokens_per_second": round(gen_tps, 2),
        "measured_prompt_tokens_per_second": round(prompt_tps, 2),
        "achieved_read_gbps": round(achieved_gbps, 2),
        "runner_copy_gbps": round(copy, 2),
        "efficiency": round(efficiency, 3),
        "lee_cpu_bandwidth_gbps": LEE_CPU_BANDWIDTH_GBPS,
        "lee_gpu_bandwidth_gbps": LEE_GPU_BANDWIDTH_GBPS,
        "bandwidth_source": "datasheet, not measured on Lee's PC",
        "unscaled_seconds": round(max(float(unscaled_s or 0), 0), 1),
    }
    if not efficiency or not file_bytes:
        out["status"] = "not_measured"
        out["note"] = "No generation rate or no copy measurement, so the job was not projected."
        return out

    def _tps(bandwidth_gbps: float) -> float:
        return efficiency * bandwidth_gbps * 1e9 / file_bytes

    cpu_gen = _tps(LEE_CPU_BANDWIDTH_GBPS)
    if file_bytes <= LEE_GPU_WEIGHT_BUDGET:
        gpu_gen = _tps(LEE_GPU_BANDWIDTH_GBPS)
        device = "vram"
    else:
        gpu_bytes = min(file_bytes, LEE_GPU_WEIGHT_BUDGET)
        cpu_bytes = file_bytes - gpu_bytes
        seconds = (gpu_bytes / (efficiency * LEE_GPU_BANDWIDTH_GBPS * 1e9)) + (
            cpu_bytes / (efficiency * LEE_CPU_BANDWIDTH_GBPS * 1e9)
        )
        gpu_gen = 1 / seconds if seconds else 0
        device = "partial_offload"
    ratio = (gpu_gen / gen_tps) if gen_tps else 0
    gpu_prompt = prompt_tps * ratio if ratio else 0
    cpu_prompt = prompt_tps * ((cpu_gen / gen_tps) if gen_tps else 0)

    def _job(gen: float, prompt: float) -> float:
        model_s = 0.0
        if completion_tokens and gen:
            model_s += completion_tokens / gen
        if prompt_tokens and prompt:
            model_s += prompt_tokens / prompt
        return model_s + max(float(unscaled_s or 0), 0)

    out.update({
        "status": "projected",
        "device": device,
        "lee_cpu_generation_tokens_per_second": round(cpu_gen, 2),
        "lee_gpu_generation_tokens_per_second": round(gpu_gen, 2),
        "lee_cpu_job_minutes": round(_job(cpu_gen, cpu_prompt) / 60, 1),
        "lee_gpu_job_minutes": round(_job(gpu_gen, gpu_prompt) / 60, 1),
        "note": (
            "Job minutes add the runner's non-model time (research and process overhead, "
            "with pull, load, prompt eval and generation removed) to the projected model time. "
            "Pull and load are not included."
        ),
    })
    return out


def format_timing_table(rows: list[dict]) -> str:
    header = (
        "| Model | Request | Pull | Load | Job | Prompt tok/s | Gen tok/s | Calls | Retries | Timeouts | Peak RAM | Peak VRAM | Lee GPU job |\n"
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    )
    lines = [header]
    for row in rows:
        lines.append(
            "| {model} | {request} | {pull} | {load} | {job} | {prompt} | {gen} | {calls} | {retries} | {timeouts} | {ram} | {vram} | {lee} |".format(
                model=row.get("model") or "",
                request=row.get("request") or "",
                pull=row.get("pull") or "",
                load=row.get("load") or "",
                job=row.get("job") or "",
                prompt=row.get("prompt_tps") or "",
                gen=row.get("gen_tps") or "",
                calls=row.get("calls") if row.get("calls") is not None else "",
                retries=row.get("retries") if row.get("retries") is not None else "",
                timeouts=row.get("timeouts") if row.get("timeouts") is not None else "",
                ram=row.get("ram") or "",
                vram=row.get("vram") or "",
                lee=row.get("lee_job") or "",
            )
        )
    return "\n".join(lines) + "\n"


def two_model_fit(chat_bytes: int, plan_bytes: int) -> dict:
    """Whether 4B chat plus a plan model fits the 16 GB machine.

    Sequential loading uses Ollama's one-model cap. Simultaneous loading adds the files.
    A single Q4 file of 9-10 GB is the practical cap already set for this machine.
    """
    chat_gb = round((chat_bytes or 0) / 1_000_000_000, 2)
    plan_gb = round((plan_bytes or 0) / 1_000_000_000, 2)
    together = round(chat_gb + plan_gb, 2)
    return {
        "chat_gb": chat_gb,
        "plan_gb": plan_gb,
        "sequential_fits": plan_gb <= 10 and chat_gb <= 10,
        "resident_together_fits": together <= 8,
        "files_together_gb": together,
        "note": (
            "Ollama can keep a single model loaded (OLLAMA_MAX_LOADED_MODELS=1) and drops it after the keep-alive, "
            "which defaults to 5 minutes. Sequential use fits when the larger file is within the 9-10 GB cap. "
            "Both resident at once also needs KV cache and Windows, Electron and Campus, so a combined file size "
            "over 8 GB is treated as not fitting with headroom."
        ),
    }
