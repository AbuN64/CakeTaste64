"""Regression for private bootstrap without an installed toolkit and source revision drift."""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bootstrap", REPO / "ci/ensure_venv.py")
assert spec is not None and spec.loader is not None
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def test_every_source_revision_is_part_of_the_key_and_failed_install_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_bytes((REPO / "pyproject.toml").read_bytes())
            heads = {}

            def lookup(argv, cwd):
                return 0, heads.get(argv[2], "a" * 40) + "\tHEAD\n"

            rows = bootstrap.requirements(root, lookup)
            first = bootstrap.key(root, rows)
            sibling = root / "another-checkout"
            sibling.mkdir()
            (sibling / "pyproject.toml").write_bytes((root / "pyproject.toml").read_bytes())
            self.assertNotEqual(first, bootstrap.key(sibling, rows))
            sources = bootstrap.tomllib.loads((root / "pyproject.toml").read_text())["tool"]["bootstrap"]["sources"]
            for url in sources.values():
                heads[url] = "b" * 40
                self.assertNotEqual(first, bootstrap.key(root, bootstrap.requirements(root, lookup)))
                heads.clear()
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
                bootstrap.ensure(root, run=run, version=(3, 13), lookup=lookup)
            self.assertFalse((root / ".venv" / bootstrap.STAMP).exists())
            with (
                mock.patch.object(
                    bootstrap.subprocess, "run", return_value=mock.Mock(returncode=128, stdout="", stderr="offline")
                ),
                self.assertRaisesRegex(RuntimeError, "ls-remote"),
            ):
                bootstrap.requirements(root)

    def test_fresh_interpreter_resolves_dependencies_without_toolkit(self):
        script = r"""
import importlib.util, sys
from pathlib import Path
root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('bootstrap', root / 'ci/ensure_venv.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
rows = module.requirements(root, lambda argv, cwd: (0, 'a' * 40 + '\tHEAD\n'))
assert any(row.startswith('abucache @ git+') for row in rows)
assert 'abucache' not in sys.modules
assert 'unbake' not in sys.modules
"""
        done = subprocess.run([sys.executable, "-I", "-S", "-c", script, str(REPO)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
