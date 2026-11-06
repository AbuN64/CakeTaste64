"""Every third-party module this repository imports is a declared dependency, so the CI environment installs it.

A module imported by src or ci that pyproject.toml's `dependencies` do not name fails here, before the CI environment
finds it missing (abucache, 11-03: `ModuleNotFoundError` at suite-import).
"""

import ast
import re
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Modules the interpreter that runs the code provides, so no requirement can install them: `gdb` exists only inside
# the gdb process that runs src/cake_taste/repro/gdb_capture*.py.
PROVIDED_BY_HOST = {"gdb"}
SCANNED = ("src", "ci")  # what runs from an installed toolkit or the deploy; tests may import only what these declare


def normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def imported(source: str) -> set[str]:
    """The top-level names of every absolute import in a Python source."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def local_names(root: Path) -> set[str]:
    """Modules this repository provides itself: its packages under src and the module files beside its scripts."""
    found = set()
    for folder in (root / "src", root / "ci", root / "tests"):
        if folder.is_dir():
            found.update(path.stem for path in folder.iterdir() if path.suffix == ".py" or path.is_dir())
    return found


def declared(root: Path) -> set[str]:
    rows = tomllib.loads((root / "pyproject.toml").read_text())["project"].get("dependencies", [])
    return {normal(re.split(r"[\s=<>!~;@\[]", row, maxsplit=1)[0]) for row in rows}


def undeclared(root: Path, extra_sources: dict[str, str] | None = None) -> dict[str, list[str]]:
    """{module: [files that import it]} for every third-party module the tree imports without declaring it."""
    sources = {
        str(path.relative_to(root)): path.read_text()
        for folder in SCANNED
        for path in sorted((root / folder).rglob("*.py"))
    }
    sources.update(extra_sources or {})
    skip = set(sys.stdlib_module_names) | local_names(root) | PROVIDED_BY_HOST
    have = declared(root)
    missing: dict[str, list[str]] = {}
    for name, source in sources.items():
        for module in sorted(imported(source) - skip):
            if normal(module) not in have:
                missing.setdefault(module, []).append(name)
    return missing


class DeclaredDependencyTests(unittest.TestCase):
    def test_every_module_the_repository_imports_is_a_declared_dependency(self):
        missing = undeclared(REPO)
        self.assertEqual(missing, {}, "imported but not in pyproject.toml dependencies: " + repr(missing))

    def test_abucache_is_declared_with_a_source_the_toolkit_resolves(self):
        project = tomllib.loads((REPO / "pyproject.toml").read_text())
        self.assertIn("abucache", declared(REPO))
        self.assertIn("abucache", project["tool"]["bootstrap"]["sources"])

    def test_an_import_missing_from_the_declaration_is_found_and_a_declared_one_is_not(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "src/pkg").mkdir(parents=True)
            (root / "ci").mkdir()
            (root / "tests").mkdir()
            (root / "src/pkg/__init__.py").write_text("")
            (root / "src/pkg/use.py").write_text("import os\nimport abucache\nfrom pkg import x\nfrom . import y\n")
            (root / "ci/step.py").write_text("from other_dep.sub import thing\nimport tomllib\n")
            (root / "pyproject.toml").write_text('[project]\nname = "p"\ndependencies = ["abu-cache>=1"]\n')
            self.assertEqual(undeclared(root), {"abucache": ["src/pkg/use.py"], "other_dep": ["ci/step.py"]})
            (root / "pyproject.toml").write_text('[project]\nname = "p"\ndependencies = ["abucache", "other_dep"]\n')
            self.assertEqual(undeclared(root), {})
            runner = {"runner": "from abucache import stages\nimport nope"}
            self.assertEqual(undeclared(root, runner), {"nope": ["runner"]})

    def test_the_declared_requirement_names_are_read_from_every_requirement_form(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            rows = ["a==1", "B_c>=2", "d[extra]", "e @ git+https://example.lan/e@" + "a" * 40, 'f; python_version>"3"']
            (root / "pyproject.toml").write_text("[project]\nname = 'p'\ndependencies = " + repr(rows) + "\n")
            self.assertEqual(declared(root), {"a", "b-c", "d", "e", "f"})


if __name__ == "__main__":
    unittest.main()
