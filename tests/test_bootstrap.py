"""Regression for private bootstrap without an installed toolkit and source revision drift."""

import importlib.util
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bootstrap", REPO / "ci/ensure_venv.py")
assert spec is not None and spec.loader is not None
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def test_every_source_is_pinned_to_its_commit_part_of_the_key_and_failed_install_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_bytes((REPO / "pyproject.toml").read_bytes())
            sources = bootstrap.tomllib.loads((root / "pyproject.toml").read_text())["tool"]["bootstrap"]["sources"]
            rows = bootstrap.requirements(root)
            for name, pin in sources.items():
                url, commit = pin.rsplit("@", 1)
                self.assertIn(f"{name} @ git+{url}@{commit}", rows)
            first = bootstrap.key(root, rows)
            sibling = root / "another-checkout"
            sibling.mkdir()
            (sibling / "pyproject.toml").write_bytes((root / "pyproject.toml").read_bytes())
            self.assertNotEqual(first, bootstrap.key(sibling, rows))
            for pin in sources.values():  # a new commit for any source is a new environment
                moved = (root / "pyproject.toml").read_text().replace(pin, pin[:-40] + "b" * 40)
                (root / "pyproject.toml").write_text(moved)
                self.assertNotEqual(first, bootstrap.key(root, bootstrap.requirements(root)))
                (root / "pyproject.toml").write_bytes((REPO / "pyproject.toml").read_bytes())
            calls = []

            def run(argv, **kwargs):
                calls.append(argv)
                if "venv" in argv:
                    python = root / ".venv/bin/python"
                    python.parent.mkdir(parents=True, exist_ok=True)
                    python.touch()
                elif "pip" in argv:
                    raise RuntimeError("install failed")

            with self.assertRaisesRegex(RuntimeError, "install failed"):
                bootstrap.ensure(root, run=run, version=(3, 13))
            self.assertFalse((root / ".venv" / bootstrap.STAMP).exists())

    def test_the_unbake_environment_holds_only_unbake_so_it_brings_the_abucache_it_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_bytes((REPO / "pyproject.toml").read_bytes())
            calls = []

            def run(argv, **kwargs):
                calls.append(argv)
                if "venv" in argv:
                    (root / ".venv-unbake/bin").mkdir(parents=True, exist_ok=True)
                    (root / ".venv-unbake/bin/python").touch()

            python = bootstrap.ensure(root, run=run, version=(3, 13), tool=True)
            self.assertEqual(python, root / ".venv-unbake/bin/python")
            installs = [c for c in calls if "pip" in c]
            self.assertEqual(len(installs), 1, installs)
            self.assertTrue(any(a.startswith("unbake @ git+") for a in installs[0]))
            self.assertFalse(any("abucache" in a or a == "-e" for a in installs[0]), installs[0])
            self.assertFalse((root / ".venv").exists())

    def test_a_source_without_an_exact_commit_refuses_instead_of_floating(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text = (REPO / "pyproject.toml").read_text()
            for floating in ("https://github.com/AbuN64/cake-unbake-64", "https://github.com/AbuN64/cake-unbake-64@main"):
                pinned = re.search(r'unbake = "([^"]+)"', text)[1]
                (root / "pyproject.toml").write_text(text.replace(pinned, floating))
                with self.assertRaisesRegex(RuntimeError, "unbake: tool.bootstrap.sources must be URL@"):
                    bootstrap.requirements(root)

    def test_fresh_interpreter_resolves_dependencies_without_toolkit(self):
        script = r"""
import importlib.util, sys
from pathlib import Path
root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('bootstrap', root / 'ci/ensure_venv.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
rows = module.requirements(root)
assert any(row.startswith('abucache @ git+') for row in rows)
assert 'abucache' not in sys.modules
assert 'unbake' not in sys.modules
"""
        done = subprocess.run([sys.executable, "-I", "-S", "-c", script, str(REPO)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
