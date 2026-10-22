"""C12 regression: no built-in keys, no literal CI key, no silent success without a mandatory key.

Before: the GPU scripts started their model server with the built-in key "ayven-validate" when
AYVEN_LOCAL_LLM_API_KEY was unset; the Windows CI workflow carried a literal AYVEN_ACCESS_KEY;
windows_campus_acceptance.py skipped login without a key and carried on; the hosted acceptance mode
ran (and could report success) without AYVEN_ACCEPTANCE_KEY.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GPU_SCRIPTS = ("gpu_employee.sh", "gpu_manager.sh", "gpu_supervisor.sh")


@unittest.skipUnless(shutil.which("bash"), "bash required")
class GpuScripts(unittest.TestCase):
    def run_script(self, name, key):
        stub = Path(tempfile.mkdtemp())
        marker = stub / "launched"
        for tool in ("python", "llama-server"):       # stand-ins: record a launch instead of starting a GPU server
            exe = stub / tool
            exe.write_text(f'#!/bin/sh\necho "$@" > "{marker}"\n')
            exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
        env = {"PATH": f"{stub}:/usr/bin:/bin", "HOME": str(stub)}
        if key is not None:
            env["AYVEN_LOCAL_LLM_API_KEY"] = key
        proc = subprocess.run(["bash", str(ROOT / "scripts" / name)], env=env, capture_output=True, text=True)
        return proc, marker

    def test_missing_or_weak_key_refuses_before_launch(self):
        for name in GPU_SCRIPTS:
            for key in (None, "", "ayven-validate", "short"):
                proc, marker = self.run_script(name, key)
                self.assertNotEqual(proc.returncode, 0, (name, key))
                self.assertFalse(marker.exists(), f"{name} launched without a valid key")
                self.assertIn("AYVEN_LOCAL_LLM_API_KEY", proc.stderr)

    def test_explicit_key_is_passed_through(self):
        key = "k" * 40
        for name in GPU_SCRIPTS:
            proc, marker = self.run_script(name, key)
            self.assertEqual(proc.returncode, 0, (name, proc.stderr))
            self.assertIn("--api-key " + key, marker.read_text())

    def test_no_built_in_key_remains(self):
        for name in GPU_SCRIPTS:
            self.assertNotIn("ayven-validate", (ROOT / "scripts" / name).read_text())


class CiWorkflow(unittest.TestCase):
    def test_no_literal_access_key(self):
        text = (ROOT / ".github" / "workflows" / "campus-windows.yml").read_text()
        self.assertNotIn("campus-windows-gate", text)
        for line in text.splitlines():
            if "AYVEN_ACCESS_KEY:" in line:
                self.assertIn("${{", line, "AYVEN_ACCESS_KEY must not be a literal")
        self.assertIn("::add-mask::", text)


class AcceptanceScriptsRequireKeys(unittest.TestCase):
    def test_hosted_acceptance_refuses_without_key(self):
        env = {k: v for k, v in os.environ.items() if k != "AYVEN_ACCEPTANCE_KEY"}
        env["PYTHONPATH"] = f"{ROOT}{os.pathsep}{ROOT / 'apps' / 'api'}"
        proc = subprocess.run([sys.executable, str(ROOT / "validation" / "acceptance_journey.py"),
                               "--hosted", "http://127.0.0.1:9"], env=env, capture_output=True, text=True, timeout=120)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("AYVEN_ACCEPTANCE_KEY", proc.stdout + proc.stderr)

    def test_windows_acceptance_login_refuses_without_key(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import importlib
        os.environ.pop("AYVEN_ACCESS_KEY", None)
        module = importlib.import_module("windows_campus_acceptance")
        module = importlib.reload(module)
        with self.assertRaises(SystemExit):
            module.login(object())


if __name__ == "__main__":
    unittest.main()
