"""Record the machine the eval is actually running on, including a copy-bandwidth measurement."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        return ""
    return ""


def _ram_bytes() -> int:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except OSError:
        return 0
    return 0


def _gpu() -> str:
    try:
        return subprocess.check_output(["nvidia-smi", "-L"], text=True, stderr=subprocess.DEVNULL, timeout=15).strip()
    except (OSError, subprocess.SubprocessError):
        return "none"


def _copy_gbps() -> float:
    source = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
int main(void) {
    size_t n = (size_t)256 << 20;
    char *a = malloc(n);
    char *b = malloc(n);
    if (!a || !b) return 1;
    memset(a, 1, n);
    struct timespec s, e;
    clock_gettime(CLOCK_MONOTONIC, &s);
    double bytes = 0;
    for (int i = 0; i < 8; i++) {
        memcpy(b, a, n);
        bytes += (double)n;
    }
    clock_gettime(CLOCK_MONOTONIC, &e);
    double sec = (double)(e.tv_sec - s.tv_sec) + (double)(e.tv_nsec - s.tv_nsec) / 1e9;
    if (sec <= 0) return 1;
    printf("%.3f\n", bytes / sec / 1e9);
    return 0;
}
"""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "copy.c"
            bin_path = Path(tmp) / "copy"
            src.write_text(source, encoding="utf-8")
            subprocess.check_call(["gcc", "-O2", str(src), "-o", str(bin_path)], timeout=60)
            out = subprocess.check_output([str(bin_path)], text=True, timeout=60).strip()
            return float(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0.0


def main() -> int:
    out = Path(os.environ.get("AYVEN_EVAL_OUT", "eval-artifacts"))
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "cpu_model": _cpu_model(),
        "cores": os.cpu_count() or 0,
        "ram_bytes": _ram_bytes(),
        "gpu": _gpu(),
        "copy_gbps": round(_copy_gbps(), 3),
        "ollama_no_gpu": os.environ.get("OLLAMA_NO_GPU", ""),
    }
    (out / "runner.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        f"runner cpu={payload['cpu_model']} cores={payload['cores']} ram_bytes={payload['ram_bytes']} gpu={payload['gpu']} copy_gbps={payload['copy_gbps']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
