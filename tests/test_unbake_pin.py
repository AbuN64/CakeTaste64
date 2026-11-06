"""The oracle's generated Makefile and selected unbake share the four-argument keep_dates contract."""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests/fixtures/decomp_keep_dates.mk"
MAKEFILE_LINES = (127, 137)  # the selected unbake template lines the fixture holds


def makefile_call_shapes(makefile: str) -> list[int]:
    """The argument count of every keep_dates.py call in a Makefile's recipe lines."""
    shapes = []
    for line in makefile.splitlines():
        call = re.search(r"keep_dates\.py((?:\s+[^\s;&|]+)*)", line) if not line.startswith("#") else None
        if call:
            shapes.append(len(call[1].split()))
    return shapes


def script_arity(source: str) -> int:
    """The argument count keep_dates.py unpacks from sys.argv on its first line that reads them."""
    for line in source.splitlines():
        if line.lstrip().startswith("#") or "sys.argv" not in line.split("#")[0]:
            continue
        code = line.split("#")[0]
        span = re.search(r"sys\.argv\[(\d+):(\d+)\]", code)
        return int(span[2]) - int(span[1]) if span else len(re.findall(r"sys\.argv\[\d+\]", code))
    raise AssertionError("keep_dates.py reads no sys.argv")


class UnbakePinTests(unittest.TestCase):
    def test_the_fixture_matches_the_selected_tool_template_and_four_argument_calls(self):
        import tomllib

        sources = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["bootstrap"]["sources"]
        text = FIXTURE.read_text()
        self.assertIn(sources["unbake"].rsplit("@", 1)[1][:8], text)
        self.assertEqual(makefile_call_shapes(text), [4, 4])
        found = list(REPO.glob(".venv-unbake/lib/python*/site-packages/unbake/resources/templates/Makefile.in"))
        self.assertEqual(len(found), 1)
        template = found[0].read_text().splitlines()
        self.assertEqual(text.splitlines()[1:], [template[n - 1] for n in MAKEFILE_LINES])

    def test_makefile_refresh_preserves_current_output_and_discards_a_retired_generator(self):
        import subprocess
        import tempfile

        python = REPO / ".venv-unbake/bin/python"
        helper = REPO / "hosts/n64recomp/refresh_makefile.py"
        code = "from unbake.build import generator_version; print(generator_version())"
        current = subprocess.run([str(python), "-c", code], capture_output=True, text=True, check=True).stdout.strip()
        with tempfile.TemporaryDirectory() as tmp:
            makefile = Path(tmp) / "Makefile"
            for stamp, retained in ((current, True), ("retired", False)):
                text = f"# generator: {stamp}\nall:\n"
                makefile.write_text(text)
                subprocess.run([str(python), str(helper), str(makefile)], check=True)
                self.assertEqual(makefile.exists(), retained)
                if retained:
                    self.assertEqual(makefile.read_text(), text)
            subprocess.run([str(python), str(helper), str(makefile)], check=True)
            self.assertFalse(makefile.exists())

    def test_selected_unbake_has_no_transitive_cache_revision_pin(self):
        import json
        import subprocess

        python = REPO / ".venv-unbake/bin/python"
        code = "import importlib.metadata as m,json; print(json.dumps(m.requires('unbake')))"
        done = subprocess.run([str(python), "-c", code], capture_output=True, text=True, check=True)
        cache = [row for row in json.loads(done.stdout) if row.startswith("abucache")]
        self.assertEqual(cache, ["abucache"])

    def test_the_venv_unbake_keep_dates_takes_the_arguments_the_generated_makefile_passes(self):
        found = list(REPO.glob(".venv-unbake/lib/python*/site-packages/unbake/resources/make/keep_dates.py"))
        self.assertEqual(len(found), 1, "no .venv-unbake: run `python3 ci/ensure_venv.py unbake`")
        installed = found[0].read_text()
        wanted = set(makefile_call_shapes(FIXTURE.read_text()))
        self.assertEqual(
            {script_arity(installed)},
            wanted,
            "the selected unbake and its generated Makefile disagree about keep_dates arguments",
        )

    def test_a_retired_two_argument_keep_dates_fails_the_check(self):
        two = "mode, version = sys.argv[1], sys.argv[2]\n"  # CakeUnbake64 f675641, line 7
        four = "asm_root, build, mode, version = sys.argv[1:5]  # the layout's asm and build roots\n"  # 10d7440
        self.assertEqual((script_arity(two), script_arity(four)), (2, 4))
        self.assertNotEqual({script_arity(two)}, set(makefile_call_shapes(FIXTURE.read_text())))

    def test_oracle_tool_remains_pinned_while_cache_resolves_landed_source(self):
        import tomllib

        sources = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["bootstrap"]["sources"]
        self.assertRegex(sources["unbake"], r"^https://\S+@[0-9a-f]{40}$")
        self.assertEqual(sources["abucache"], "https://github.com/AbuN64/cache-64")


if __name__ == "__main__":
    unittest.main()
