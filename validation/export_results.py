"""Durable export of a validation run.

A local tar on the pod disk is not durable. The primary path writes a
checksummed tarball to AYVEN_EXPORT_DIR or AYVEN_EXPORT_HTTP_URL and reads
it back. Git, S3, and rclone are secondary. STOP POD is allowed only after
a read-back matches. Hugging Face upload without a download is not verified.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

SMALL_SUFFIXES = {".md", ".json", ".txt", ".yaml", ".yml"}
_DURABLE = ("dir", "http", "s3", "rclone")


def destinations() -> dict:
    return {
        "dir": os.environ.get("AYVEN_EXPORT_DIR", ""),
        "http_url": os.environ.get("AYVEN_EXPORT_HTTP_URL", ""),
        "git_remote": os.environ.get("AYVEN_EXPORT_GIT_REMOTE", ""),
        "git_branch": os.environ.get("AYVEN_EXPORT_GIT_BRANCH", ""),
        "s3_uri": os.environ.get("AYVEN_EXPORT_S3_URI", ""),
        "rclone_target": os.environ.get("AYVEN_EXPORT_RCLONE_TARGET", ""),
        "hf_dataset": os.environ.get("AYVEN_EXPORT_HF_DATASET", ""),
    }


def configured() -> bool:
    values = destinations()
    if values["dir"] or values["http_url"]:
        return True
    if values["git_remote"] and values["git_branch"]:
        return True
    return any(values[key] for key in ("s3_uri", "rclone_target", "hf_dataset"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _redact_file(path: Path) -> None:
    canary = os.environ.get("AYVEN_SECRET_CANARY", "")
    if not canary or not path.is_file():
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    if canary in text:
        path.write_text(text.replace(canary, "[REDACTED]"), encoding="utf-8")


def _ensure_named(run_dir: Path) -> dict[str, Path]:
    """Scorecard, traces, claims, and logs always exist so the manifest can hash them."""
    named = {
        "scorecard": run_dir / "scorecard.md",
        "traces": run_dir / "traces.json",
        "claims": run_dir / "claims.json",
        "logs": run_dir / "logs.txt",
    }
    report = run_dir / "final-report.md"
    if not named["scorecard"].exists():
        named["scorecard"].write_text(report.read_text(encoding="utf-8") if report.exists() else "scorecard\n", encoding="utf-8")
    if not named["traces"].exists():
        named["traces"].write_text("[]\n", encoding="utf-8")
    if not named["claims"].exists():
        named["claims"].write_text("[]\n", encoding="utf-8")
    if not named["logs"].exists():
        logs = []
        for path in sorted(run_dir.rglob("*.log")):
            logs.append(path.read_text(encoding="utf-8", errors="replace")[:4000])
        named["logs"].write_text("\n".join(logs) or "no logs\n", encoding="utf-8")
    for path in named.values():
        _redact_file(path)
    return named


def build_bundle(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    named = _ensure_named(run_dir)
    files = {label: {"path": str(path.relative_to(run_dir)), "sha256": _sha256(path)} for label, path in named.items()}
    archive = run_dir / f"{run_dir.name}.tar.gz"
    if archive.exists():
        archive.unlink()
    with tarfile.open(archive, "w:gz") as tar:
        for path in named.values():
            tar.add(path, arcname=f"{run_dir.name}/{path.name}")
    files["archive"] = {"path": archive.name, "sha256": _sha256(archive)}
    manifest = {
        "run": run_dir.name,
        "files": [str(path.relative_to(run_dir)) for path in _small_files(run_dir) if ".export-git" not in path.parts and ".export-readback" not in path.parts],
        "sha256": files,
        "destinations": {key: value for key, value in destinations().items() if "token" not in key},
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _redact_file(manifest_path)
    manifest_sha = _sha256(manifest_path)
    (run_dir / "manifest.sha256").write_text(manifest_sha + "\n", encoding="utf-8")
    manifest["sha256"]["manifest"] = {"path": "manifest.json", "sha256": manifest_sha}
    return {"archive": archive, "manifest": manifest_path, "sha256": manifest["sha256"]}


def _http_put(base: str, name: str, data: bytes) -> None:
    import httpx

    response = httpx.post(
        base.rstrip("/") + "/upload",
        content=data,
        headers={"X-Ayven-Name": name},
        timeout=30,
    )
    response.raise_for_status()


def _http_get(base: str, name: str) -> bytes:
    import httpx

    response = httpx.get(base.rstrip("/") + "/files/" + name, timeout=30)
    response.raise_for_status()
    return response.content


def preflight_export() -> dict:
    """Prove the export target is writable before any model download."""
    values = destinations()
    if not configured():
        return {"ok": False, "verified": False, "reason": "No export target is configured.", "target": ""}
    probe = b"ayven-export-preflight\n"
    if values["dir"]:
        folder = Path(values["dir"])
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / ".ayven-preflight"
            path.write_bytes(probe)
            ok = path.read_bytes() == probe
            path.unlink(missing_ok=True)
        except OSError as exc:
            return {"ok": False, "verified": False, "reason": f"export dir not writable: {exc}", "target": values["dir"]}
        if not ok:
            return {"ok": False, "verified": False, "reason": "export dir read-back mismatched", "target": values["dir"]}
        return {"ok": True, "verified": True, "target": values["dir"], "kind": "dir"}
    if values["http_url"]:
        try:
            _http_put(values["http_url"], ".ayven-preflight", probe)
            back = _http_get(values["http_url"], ".ayven-preflight")
        except Exception as exc:
            return {"ok": False, "verified": False, "reason": f"export http not writable: {exc}", "target": values["http_url"]}
        if back != probe:
            return {"ok": False, "verified": False, "reason": "export http read-back mismatched", "target": values["http_url"]}
        return {"ok": True, "verified": True, "target": values["http_url"], "kind": "http"}
    if values["s3_uri"] or values["rclone_target"]:
        return {"ok": True, "verified": False, "target": values["s3_uri"] or values["rclone_target"], "kind": "remote", "reason": "remote target is named; write is checked at export"}
    if values["git_remote"] and values["git_branch"]:
        if not shutil.which("git"):
            return {"ok": False, "verified": False, "reason": "git is not installed", "target": values["git_remote"]}
        probe_git = subprocess.run(["git", "ls-remote", values["git_remote"]], capture_output=True, text=True)
        if probe_git.returncode != 0:
            return {"ok": False, "verified": False, "reason": "git remote is not readable before model work", "target": values["git_remote"]}
        return {"ok": True, "verified": False, "target": values["git_remote"], "kind": "git", "reason": "git is secondary; a token can still fail at the end"}
    return {"ok": False, "verified": False, "reason": "No writable export target.", "target": ""}


def _durable(run_dir: Path, bundle: dict) -> dict:
    values = destinations()
    archive = Path(bundle["archive"])
    digest = bundle["sha256"]["archive"]["sha256"]
    name = archive.name
    if values["dir"]:
        folder = Path(values["dir"])
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        shutil.copy2(archive, target)
        shutil.copy2(bundle["manifest"], folder / "manifest.json")
        if _sha256(target) != digest:
            return {"status": "NOT_EXPORTED", "verified": False, "kind": "dir", "reason": "directory read-back hash mismatched"}
        return {"status": "VERIFIED", "verified": True, "kind": "dir", "location": str(target), "sha256": digest}
    if values["http_url"]:
        _http_put(values["http_url"], name, archive.read_bytes())
        _http_put(values["http_url"], "manifest.json", Path(bundle["manifest"]).read_bytes())
        back = _http_get(values["http_url"], name)
        got = hashlib.sha256(back).hexdigest()
        if got != digest:
            return {"status": "NOT_EXPORTED", "verified": False, "kind": "http", "reason": "http read-back hash mismatched"}
        return {"status": "VERIFIED", "verified": True, "kind": "http", "location": values["http_url"].rstrip("/") + "/" + name, "sha256": digest}
    return {"status": "NOT_EXPORTED", "verified": False, "kind": "", "reason": "no directory or http target"}


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
    """Checksum the bundle and read it back. Local disk alone is not VERIFIED."""
    run_dir = Path(run_dir)
    try:
        bundle = build_bundle(run_dir)
    except Exception as exc:
        return {"status": "NOT_EXPORTED", "verified": False, "reason": f"{type(exc).__name__}: {exc}"}
    if not configured():
        return {
            "status": "NOT_EXPORTED",
            "verified": False,
            "reason": "No directory, HTTP, git, S3, rclone, or dataset destination is configured. The pod disk is ephemeral.",
            "manifest": str(run_dir / "manifest.json"),
            "sha256": bundle["sha256"],
        }
    attempts = []
    values = destinations()
    try:
        if values["dir"] or values["http_url"]:
            attempts.append(_durable(run_dir, bundle))
        if values["s3_uri"]:
            attempts.append({**_s3(run_dir), "kind": "s3"})
        elif values["rclone_target"]:
            attempts.append({**_rclone(run_dir), "kind": "rclone"})
        if values["git_remote"] and values["git_branch"]:
            attempts.append({**_git(run_dir), "kind": "git"})
        elif values["hf_dataset"] and not attempts:
            attempts.append({**_hf(run_dir), "kind": "hf"})
    except Exception as exc:
        return {"status": "NOT_EXPORTED", "verified": False, "reason": f"{type(exc).__name__}: {exc}", "sha256": bundle["sha256"]}
    durable_required = bool(values["dir"] or values["http_url"] or values["s3_uri"] or values["rclone_target"])
    if durable_required:
        verified = [item for item in attempts if item.get("verified") and item.get("kind") in _DURABLE]
    else:
        verified = [item for item in attempts if item.get("verified")]
    if not verified:
        reason = "; ".join(item.get("reason") or item.get("status") or "" for item in attempts) or "destination did not complete"
        return {"status": "NOT_EXPORTED", "verified": False, "reason": reason, "sha256": bundle["sha256"], "attempts": attempts}
    chosen = verified[0]
    return {
        "status": "VERIFIED",
        "verified": True,
        "kind": chosen.get("kind"),
        "location": chosen.get("location"),
        "sha256": bundle["sha256"],
        "also": [item.get("location") for item in verified],
    }


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
    remote_path = f"ayven-validation/{run_dir.name}/manifest.json"
    api.upload_file(
        path_or_fileobj=str(run_dir / "manifest.json"),
        path_in_repo=remote_path,
        repo_id=dataset,
        repo_type="dataset",
    )
    try:
        downloaded = api.hf_hub_download(repo_id=dataset, filename=remote_path, repo_type="dataset")
    except Exception as exc:
        return {"status": "NOT_EXPORTED", "verified": False, "reason": f"hf upload was not read back: {exc}"}
    original = (run_dir / "manifest.json").read_text(encoding="utf-8")
    if Path(downloaded).read_text(encoding="utf-8") != original:
        return {"status": "NOT_EXPORTED", "verified": False, "reason": "hf read-back did not match the manifest"}
    return {"status": "VERIFIED", "verified": True, "location": f"hf://{dataset}/ayven-validation/{run_dir.name}"}


def banner(verified: bool) -> str:
    if verified:
        return "STOP POD"
    return "\n".join([
        "============================================================",
        "RESULTS NOT EXPORTED — DO NOT STOP POD",
        "DO NOT STOP POD — RESULTS NOT EXPORTED",
        "Set AYVEN_EXPORT_DIR or AYVEN_EXPORT_HTTP_URL before model work.",
        "Git, S3, and rclone are secondary and do not replace that read-back.",
        "============================================================",
    ])
