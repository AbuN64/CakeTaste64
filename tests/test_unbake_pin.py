"""The unbake in the private venv must call keep_dates.py the way the PINS decomp Makefile does.

The oracle recompile runs `unbake setup` on RageWarsDecomp at the PINS commit, whose generated Makefile calls
keep_dates.py with two arguments (`save|restore VERSION`). A floating unbake once took four (CakeUnbake64 10d7440) and
the recompile died at keep_dates.py line 7 with `not enough values to unpack`.
tests/fixtures/decomp_keep_dates.mk is the slice of that Makefile; the keep_dates.py of the unbake environment
(.venv-unbake, the one ci/oracle runs) is read from the installed unbake.
"""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests/fixtures/decomp_keep_dates.mk"
MAKEFILE_LINES = (454, 464)  # the lines of the PINS decomp Makefile the fixture holds


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
    def test_the_fixture_is_the_pinned_decomp_makefile_slice_with_two_calls_of_two_arguments(self):
        pins = dict(row.split() for row in (REPO / "hosts/n64recomp/PINS").read_text().splitlines())
        text = FIXTURE.read_text()
        self.assertIn(f"RageWarsDecomp {pins['RageWars'][:8]}", text, "moving the RageWars pin needs a new fixture")
        self.assertEqual(makefile_call_shapes(text), [2, 2])
        self.assertIn(f"Makefile:{MAKEFILE_LINES[0]} and :{MAKEFILE_LINES[1]}", text)

    def test_the_venv_unbake_keep_dates_takes_the_arguments_the_pinned_decomp_makefile_passes(self):
        found = list(REPO.glob(".venv-unbake/lib/python*/site-packages/unbake/resources/make/keep_dates.py"))
        self.assertEqual(len(found), 1, "no .venv-unbake: run `python3 ci/ensure_venv.py unbake`")
        installed = found[0].read_text()
        wanted = set(makefile_call_shapes(FIXTURE.read_text()))
        self.assertEqual(
            {script_arity(installed)},
            wanted,
            "the venv's unbake keep_dates.py (line 7) unpacks a different number of arguments than the PINS decomp "
            "Makefile (:454, :464) passes: pin pyproject.toml's unbake to a commit whose `unbake sync --makefile` "
            "equals that Makefile",
        )

    def test_a_four_argument_keep_dates_fails_the_check(self):
        two = "mode, version = sys.argv[1], sys.argv[2]\n"  # CakeUnbake64 f675641, line 7
        four = "asm_root, build, mode, version = sys.argv[1:5]  # the layout's asm and build roots\n"  # 10d7440
        self.assertEqual((script_arity(two), script_arity(four)), (2, 4))
        self.assertNotEqual({script_arity(four)}, set(makefile_call_shapes(FIXTURE.read_text())))

    def test_every_source_dependency_names_an_exact_commit(self):
        import tomllib

        sources = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["bootstrap"]["sources"]
        for name, url in sources.items():
            self.assertRegex(url, r"^https://\S+@[0-9a-f]{40}$", f"{name} floats: pin it as URL@<commit>")


if __name__ == "__main__":
    unittest.main()
