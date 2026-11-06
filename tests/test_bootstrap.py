"""Private bootstrap resolves landed cache source for both consumer environments."""

import importlib.util
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


def head(argv, root):
    assert argv == ["git", "ls-remote", "https://github.com/AbuN64/cache-64", "HEAD"]
    return 0, "a" * 40 + "\tHEAD\n"


class BootstrapTests(unittest.TestCase):
    def test_landed_cache_revision_and_checkout_are_part_of_the_key(self):
        rows = bootstrap.requirements(REPO, lookup=head)
        self.assertIn("abucache @ git+https://github.com/AbuN64/cache-64@" + "a" * 40, rows)
        first = bootstrap.key(REPO, rows)
        moved = bootstrap.requirements(REPO, lookup=lambda *args: (0, "b" * 40 + "\tHEAD\n"))
        self.assertNotEqual(first, bootstrap.key(REPO, moved))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_bytes((REPO / "pyproject.toml").read_bytes())
            self.assertNotEqual(first, bootstrap.key(root, rows))

    def test_failed_or_missing_source_head_refuses_before_creating_either_environment(self):
        for tool in (False, True):
            for result in ((1, "source unavailable"), (0, ""), (0, "invalid\tHEAD\n")):
                with self.subTest(tool=tool, result=result):
                    calls = []
                    with self.assertRaisesRegex(RuntimeError, "abucache: git ls-remote HEAD failed"):
                        bootstrap.ensure(REPO, run=lambda *a, calls=calls, **k: calls.append(a), tool=tool,
                                         lookup=lambda *a, result=result: result)
                    self.assertEqual(calls, [])

    def test_both_environments_resolve_cache_with_tools_and_refresh_on_source_head_changes(self):
        for tool in (False, True):
            with self.subTest(tool=tool), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "pyproject.toml").write_bytes((REPO / "pyproject.toml").read_bytes())
                calls = []

                def run(argv, calls=calls, **kwargs):
                    calls.append(argv)
                    if "venv" in argv:
                        python = Path(argv[-1]) / "bin/python"
                        python.parent.mkdir(parents=True, exist_ok=True)
                        python.touch()

                python = bootstrap.ensure(root, run=run, version=(3, 13), tool=tool, lookup=head)
                self.assertEqual(python, root / (bootstrap.TOOL_VENV if tool else bootstrap.VENV) / "bin/python")
                installs = [c for c in calls if "pip" in c]
                self.assertEqual(len(installs), 1 if tool else 2)
                self.assertIn("abucache @ git+https://github.com/AbuN64/cache-64@" + "a" * 40, installs[0])
                if tool:
                    self.assertTrue(any(a.startswith("unbake @ git+") for a in installs[0]))
                    self.assertNotIn("-e", installs[0])
                calls.clear()
                bootstrap.ensure(root, run=run, version=(3, 13), tool=tool, lookup=head)
                self.assertEqual(calls, [])
                bootstrap.ensure(root, run=run, version=(3, 13), tool=tool,
                                 lookup=lambda *a: (0, "b" * 40 + "\tHEAD\n"))
                self.assertTrue(any("venv" in c for c in calls))

    def test_failed_cache_install_is_not_stamped_in_either_environment(self):
        for tool in (False, True):
            with self.subTest(tool=tool), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "pyproject.toml").write_bytes((REPO / "pyproject.toml").read_bytes())

                def run(argv, **kwargs):
                    if "venv" in argv:
                        Path(argv[-1]).mkdir()
                    if "pip" in argv:
                        raise RuntimeError("cache install failed")

                with self.assertRaisesRegex(RuntimeError, "cache install failed"):
                    bootstrap.ensure(root, run=run, version=(3, 13), tool=tool, lookup=head)
                self.assertFalse((root / (bootstrap.TOOL_VENV if tool else bootstrap.VENV) / bootstrap.STAMP).exists())

    def test_fresh_interpreter_resolves_dependencies_without_toolkit(self):
        script = r"""
import importlib.util, sys
from pathlib import Path
root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('bootstrap', root / 'ci/ensure_venv.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
rows = module.requirements(root, lookup=lambda *a: (0, 'a' * 40 + '\tHEAD\n'))
assert any(row.startswith('abucache @ git+') for row in rows)
assert 'abucache' not in sys.modules
assert 'unbake' not in sys.modules
"""
        done = subprocess.run([sys.executable, "-I", "-S", "-c", script, str(REPO)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
