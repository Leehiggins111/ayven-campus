"""C3, Campus side: exact per-order file allow-lists and an independent check of the returned patch.

Campus never trusts a worker's claim about which files it changed. It reads the paths from the
patch artifact itself (extended git headers only; hunk content cannot be mistaken for a header) and
compares them with the allow-list fixed when the owner created the order.
"""
import re

MAX_FILES = 50
MAX_PATH = 255
_GLOB = re.compile(r"[*?\[\]{}]")
# Plain ASCII alphabet only: rejects homoglyphs, zero-width/bidi characters, spaces and controls.
_SAFE = re.compile(r"[A-Za-z0-9._/-]+")
_PROHIBITED_NAMES = {".git", ".gitattributes", ".gitmodules"}
_PROHIBITED_TOP = {".github"}
PATCH_ARTIFACT = "changes.diff"


class ScopeError(ValueError):
    pass


def check_path(path):
    if not isinstance(path, str) or not path or len(path) > MAX_PATH:
        raise ScopeError(f"invalid path {path!r}")
    if not _SAFE.fullmatch(path):
        raise ScopeError(f'path may use only ASCII letters, digits, ".", "_", "-" and "/": {ascii(path)}')
    if path.startswith("/"):
        raise ScopeError(f"path must be relative with forward slashes: {path!r}")
    if _GLOB.search(path):
        raise ScopeError(f"globs are not allowed: {path!r}")
    if path.endswith("/"):
        raise ScopeError(f"directories are not allowed, name exact files: {path!r}")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ScopeError(f'path must not contain empty, "." or ".." parts: {path!r}')
    if _PROHIBITED_NAMES.intersection(parts) or parts[0] in _PROHIBITED_TOP:
        raise ScopeError(f"path is prohibited: {path!r}")
    return path


def normalize(entries):
    if entries is None:
        raise ScopeError("allowed_files is required: list the exact files this job may change")
    if not isinstance(entries, (list, tuple)) or not entries:
        raise ScopeError("allowed_files must be a non-empty list of file paths")
    if len(entries) > MAX_FILES:
        raise ScopeError(f"allowed_files may name at most {MAX_FILES} files")
    clean = [check_path(item) for item in entries]
    if len(set(clean)) != len(clean):
        raise ScopeError("allowed_files contains duplicates")
    if len({p.casefold() for p in clean}) != len(clean):
        raise ScopeError("allowed_files contains paths that differ only by letter case")
    return sorted(clean)


def patch_paths(diff):
    """(paths, problems) for a unified git diff. Quoted/unparseable headers, symlinks and submodules
    are problems: the patch cannot be verified and fails closed."""
    paths, problems = set(), []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            match = re.match(r"diff --git a/(\S+) b/(\S+)$", line)
            if not match:
                problems.append("unparseable diff header")
                continue
            paths.update(match.groups())
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            paths.add(line.split(" ", 2)[2])
        elif re.match(r"(new file mode|new mode|deleted file mode|old mode) (120000|160000)", line):
            problems.append("symlink or submodule change")
    return sorted(paths), problems


def verify_patch(allowed, artifacts):
    """Return [] when a PASS result's patch stays inside the allow-list, else the reasons it does not.
    artifacts: {name: bytes}. A PASS without a verifiable patch is not accepted."""
    if not allowed:
        return ["Order has no allow-list; its scope cannot be verified"]
    raw = artifacts.get(PATCH_ARTIFACT)
    if raw is None:
        return [f"PASS result has no {PATCH_ARTIFACT} patch to verify"]
    try:
        diff = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ["Patch is not UTF-8 text"]
    paths, problems = patch_paths(diff)
    if not paths:
        problems.append("Patch changes no files")
    folded = {}
    for path in paths:
        if not _SAFE.fullmatch(path):
            problems.append(f"Path is not plain ASCII: {ascii(path)}")
        folded.setdefault(path.casefold(), []).append(path)
    problems += [f"Paths differ only by letter case: {', '.join(names)}" for names in folded.values() if len(names) > 1]
    problems += [f"Unauthorised change: {ascii(path)[1:-1]}" for path in paths if path not in allowed]
    return problems
