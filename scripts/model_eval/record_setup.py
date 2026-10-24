"""Merge setup seconds into eval-artifacts/setup.json. Does not touch job time."""

from __future__ import annotations

import json
import os
from pathlib import Path


def main() -> int:
    out = Path(os.environ.get("AYVEN_EVAL_OUT", "eval-artifacts"))
    out.mkdir(parents=True, exist_ok=True)
    path = out / "setup.json"
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    for key in ("pip_s", "ollama_install_s", "pull_s"):
        raw = (os.environ.get(key.upper()) or "").strip()
        if raw:
            data[key] = float(raw)
    if os.environ.get("AYVEN_EVAL_MODEL"):
        data["model"] = os.environ["AYVEN_EVAL_MODEL"]
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print("setup " + " ".join(f"{key}={data.get(key)}" for key in ("pip_s", "ollama_install_s", "pull_s")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
