"""Durable export of a validation run.

A local tar on the pod disk is not durable. STOP POD is allowed only after a
configured destination accepts the small text artifacts and a read-back matches.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

SMALL_SUFFIXES = {".md", ".json", ".txt", ".yaml", ".yml"}


def destinations() -> dict:
    return {
        "git_remote": os.environ.get("AYVEN_EXPORT_GIT_REMOTE", ""),
        "git_branch": os.environ.get("AYVEN_EXPORT_GIT_BRANCH", ""),
        "s3_uri": os.environ.get("AYVEN_EXPORT_S3_URI", ""),
        "rclone_target": os.environ.get("AYVEN_EXPORT_RCLONE_TARGET", ""),
        "hf_dataset": os.environ.get("AYVEN_EXPORT_HF_DATASET", ""),
    }


def configured() -> bool:
    values = destinations()
    if values["git_remote"] and values["git_branch"]:
        return True
    return any(values[key] for key in ("s3_uri", "rclone_target", "hf_dataset"))


def status_line() -> str:
    if configured():
        return "CONFIGURED"
    return "NOT CONFIGURED"


def _small_files(run_dir: Path) -> list[Path]:
    files = []
    for path in run_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in SMALL_SUFFIXES and path.stat().st_size < 2_000_000:
            files.append(path)
    return files


def verify_export(run_dir: Path) -> dict:
    """Write a manifest and try the configured destination. Local disk alone is not VERIFIED."""
    run_dir = Path(run_dir)
    manifest = {
        "run": run_dir.name,
        "files": [str(path.relative_to(run_dir)) for path in _small_files(run_dir)],
        "destinations": destinations(),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if not configured():
        return {
            "status": "NOT_EXPORTED",
            "verified": False,
            "reason": "No git, S3, rclone, or dataset destination is configured. The pod disk is ephemeral.",
            "manifest": str(run_dir / "manifest.json"),
        }
    try:
        if destinations()["s3_uri"]:
            return _s3(run_dir)
        if destinations()["rclone_target"]:
            return _rclone(run_dir)
        if destinations()["git_remote"]:
            return _git(run_dir)
        if destinations()["hf_dataset"]:
            return _hf(run_dir)
    except Exception as exc:
        return {"status": "NOT_EXPORTED", "verified": False, "reason": f"{type(exc).__name__}: {exc}"}
    return {"status": "NOT_EXPORTED", "verified": False, "reason": "destination did not complete"}


def _s3(run_dir: Path) -> dict:
    uri = destinations()["s3_uri"].rstrip("/") + "/" + run_dir.name + "/manifest.json"
    if not shutil.which("aws"):
        return {"status": "NOT_EXPORTED", "verified": False, "reason": "aws cli is not installed"}
    subprocess.run(["aws", "s3", "cp", str(run_dir / "manifest.json"), uri], check=True, capture_output=True, text=True)
    read = subprocess.run(["aws", "s3", "cp", uri, "-"], check=True, capture_output=True, text=True)
    if run_dir.name not in read.stdout:
        return {"status": "NOT_EXPORTED", "verified": False, "reason": "s3 read-back did not match"}
    return {"status": "VERIFIED", "verified": True, "location": uri}


def _rclone(run_dir: Path) -> dict:
    if not shutil.which("rclone"):
        return {"status": "NOT_EXPORTED", "verified": False, "reason": "rclone is not installed"}
    target = destinations()["rclone_target"].rstrip("/") + "/" + run_dir.name
    subprocess.run(["rclone", "copy", str(run_dir / "manifest.json"), target], check=True, capture_output=True, text=True)
    shown = subprocess.run(["rclone", "cat", target + "/manifest.json"], check=True, capture_output=True, text=True)
    if run_dir.name not in shown.stdout:
        return {"status": "NOT_EXPORTED", "verified": False, "reason": "rclone read-back did not match"}
    return {"status": "VERIFIED", "verified": True, "location": target}


def _git(run_dir: Path) -> dict:
    """Push the small artifact bundle and read it back. A local bare repo is a valid remote."""
    remote = destinations()["git_remote"]
    branch = destinations()["git_branch"] or "ayven-results"
    if not shutil.which("git"):
        return {"status": "NOT_EXPORTED", "verified": False, "reason": "git is not installed"}
    token = os.environ.get("AYVEN_EXPORT_GIT_TOKEN", "")
    push_remote = remote
    if token and remote.startswith("https://") and "@" not in remote.split("://", 1)[-1].split("/")[0]:
        push_remote = remote.replace("https://", f"https://x-access-token:{token}@", 1)
    work = Path(run_dir) / ".export-git"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "Ayven", "GIT_AUTHOR_EMAIL": "ayven@localhost", "GIT_COMMITTER_NAME": "Ayven", "GIT_COMMITTER_EMAIL": "ayven@localhost"}
    subprocess.run(["git", "init", str(work)], check=True, capture_output=True, text=True, env=env)
    subprocess.run(["git", "-C", str(work), "checkout", "-b", branch], check=True, capture_output=True, text=True, env=env)
    bundle = work / run_dir.name
    bundle.mkdir(parents=True, exist_ok=True)
    for path in _small_files(run_dir):
        if ".export-git" in path.parts:
            continue
        target = bundle / path.relative_to(run_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    subprocess.run(["git", "-C", str(work), "add", run_dir.name], check=True, capture_output=True, text=True, env=env)
    subprocess.run(["git", "-C", str(work), "commit", "-m", f"Ayven validation {run_dir.name}"], check=True, capture_output=True, text=True, env=env)
    subprocess.run(["git", "-C", str(work), "push", push_remote, f"HEAD:refs/heads/{branch}"], check=True, capture_output=True, text=True, env=env)
    readback = Path(run_dir) / ".export-readback"
    if readback.exists():
        shutil.rmtree(readback)
    subprocess.run(["git", "clone", "--branch", branch, remote, str(readback)], check=True, capture_output=True, text=True, env=env)
    original = (run_dir / "manifest.json").read_text(encoding="utf-8")
    copied = readback / run_dir.name / "manifest.json"
    if not copied.is_file() or copied.read_text(encoding="utf-8") != original:
        return {"status": "NOT_EXPORTED", "verified": False, "reason": "git read-back did not match the manifest"}
    return {"status": "VERIFIED", "verified": True, "location": f"{remote}#{branch}/{run_dir.name}"}


def _hf(run_dir: Path) -> dict:
    dataset = destinations()["hf_dataset"]
    try:
        from huggingface_hub import HfApi
    except Exception:
        return {"status": "NOT_EXPORTED", "verified": False, "reason": "huggingface_hub is not installed"}
    api = HfApi()
    api.upload_file(
        path_or_fileobj=str(run_dir / "manifest.json"),
        path_in_repo=f"ayven-validation/{run_dir.name}/manifest.json",
        repo_id=dataset,
        repo_type="dataset",
    )
    return {"status": "VERIFIED", "verified": True, "location": f"hf://{dataset}/ayven-validation/{run_dir.name}"}


def banner(verified: bool) -> str:
    if verified:
        return "STOP POD"
    return "\n".join([
        "============================================================",
        "RESULTS NOT EXPORTED — DO NOT STOP POD",
        "DO NOT STOP POD — RESULTS NOT EXPORTED",
        "Set AYVEN_EXPORT_GIT_REMOTE and AYVEN_EXPORT_GIT_BRANCH,",
        "or AYVEN_EXPORT_S3_URI, or AYVEN_EXPORT_RCLONE_TARGET,",
        "or AYVEN_EXPORT_HF_DATASET, then rerun the export.",
        "============================================================",
    ])
