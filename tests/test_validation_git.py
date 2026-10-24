"""Q&T finding 3 in the validation tools: git never hangs, and its errors never carry the export token.

Before: the results export ran git push/clone/ls-remote with no timeout (a credential prompt or stalled remote
hung the run), and a failed push surfaced CalledProcessError text that included the tokenised push URL.
"""
import subprocess
from unittest import mock

import pytest

from validation import export_results

TOKEN = "ghp_" + "s" * 36


def test_git_timeout_is_a_failure_not_a_hang():
    with mock.patch.object(export_results.subprocess, "run", side_effect=subprocess.TimeoutExpired(["git"], 300)):
        with pytest.raises(RuntimeError, match="timed out"):
            export_results._run_git(["-C", "/tmp/x", "push", f"https://x-access-token:{TOKEN}@example.invalid/r"], {})


def test_git_failure_does_not_reveal_the_push_url():
    url = f"https://x-access-token:{TOKEN}@example.invalid/r"
    failure = subprocess.CalledProcessError(128, ["git", "-C", "/tmp/x", "push", url], stderr="denied " + url)
    with mock.patch.object(export_results.subprocess, "run", side_effect=failure):
        with pytest.raises(RuntimeError) as raised:
            export_results._run_git(["-C", "/tmp/x", "push", url, "HEAD:refs/heads/b"], {})
    assert TOKEN not in str(raised.value)
    assert "push failed" in str(raised.value)


def test_git_calls_never_prompt():
    seen = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "", "")
    with mock.patch.object(export_results.subprocess, "run", side_effect=fake_run):
        export_results._run_git(["init", "/tmp/x"], {"PATH": "/usr/bin"})
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert seen["timeout"] and seen["stdin"] is subprocess.DEVNULL
