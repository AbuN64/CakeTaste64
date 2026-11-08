"""ci/oracle build resumes: a build interrupted in the host build leaves the finished steps (deps, abumas, recompile)
current, so the next build reruns none of them, and the host build is compiled through ccache with base_dir and
hash_dir in the launcher. The seams (deps, the recompile, cmake, the host) are stubs that log each call; ci/oracle is
the real script."""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ORACLE = Path(__file__).resolve().parents[1] / "ci/oracle"
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "GIT_AUTHOR_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@example.lan", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.lan"}

DEPS = """#!/bin/sh
echo deps >> "$CALLS"
for d in N64ModernRuntime rt64 N64Recomp AbuMas; do mkdir -p "$1/$d"; done
printf 'all:\\n\\t@echo abumas >> "$(CALLS)"; mkdir -p build; echo bin > build/abumas\\n' > "$1/AbuMas/Makefile"
"""
RECOMPILE = """#!/bin/sh
echo recompile >> "$CALLS"
echo "$@" > "$1/args"
gen=$1/recomp/us-rev1
mkdir -p "$gen" "$1/decomp/versions/us-rev1"
echo "x" > "$gen/symbols.toml"
echo "x" > "$gen/refused.tsv"
echo "gMain = 0x80001000; // type:func" > "$1/decomp/versions/us-rev1/symbol_addrs.txt"
echo "{}" > "$1/taste-profile.json"
git init -q "$1/decomp/.g" 2>/dev/null; git -C "$1/decomp" init -q
git -C "$1/decomp" -c user.name=t -c user.email=t@example.lan commit -q --allow-empty -m c 2>/dev/null
"""
CMAKE = """#!/bin/sh
case $1 in
  --build)
    echo host-build >> "$CALLS"
    test ! -e "$CALLS.fail" || exit 1
    mkdir -p "$2/gen"
    printf '#!/bin/sh\\necho 0123456789abcdef > "$N64R_TAS_STATE_HASH"\\n' > "$2/n64host"
    chmod +x "$2/n64host"
    echo p > "$2/gen/cpu_profile.h"; echo n > "$2/gen/cpu_native.h" ;;
  *)
    echo "configure $*" >> "$CALLS"
    cache=$4/CMakeCache.txt
    ! test -e "$cache" || grep -qx "CMAKE_HOME_DIRECTORY:INTERNAL=$PWD/$2" "$cache" || echo stale >> "$CALLS"
    mkdir -p "$4"
    echo "CMAKE_HOME_DIRECTORY:INTERNAL=$PWD/$2" > "$cache" ;;
esac
"""


def facts_module(text: str) -> str:
    return f"import sys, pathlib\npathlib.Path(sys.argv[sys.argv.index('--output') + 1]).write_text({text!r})\n"


def script(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class Resume(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        n = self.repo / "hosts/n64recomp"
        script(self.repo / "ci/oracle", ORACLE.read_text())
        discovery = Path("src/cake_taste/oracle/icd.py")
        script(self.repo / discovery, (ORACLE.parents[1] / discovery).read_text())
        script(n / "deps.sh", DEPS)
        script(n / "recompile.sh", RECOMPILE)
        for name in ("checkout.sh", "decomp_elf.sh", "recomp_profile.py", "ragewars.profile.json"):
            script(n / name, "x\n")
        script(n / "kept_audit.py", "pass\n")  # the audit of the kept libultra originals, run on the generated funcs
        (n / "TOOLCHAIN").write_text("gcc_major=14\n")
        (n / "PINS").write_text("RageWars abc\n")
        (n / "patches").mkdir()
        (n / "patches/a.diff").write_text("d\n")
        script(n / "host_config.py", "import sys, pathlib\npathlib.Path(sys.argv[1]).write_text('host')\n")
        script(self.repo / "ci/ensure_venv.py", f"print('{self.repo}/.venv/bin/python')\n")
        script(self.repo / "src/cake_taste/__main__.py", facts_module("{}"))
        (self.repo / "pyproject.toml").write_text("[project]\n")
        venv = self.repo / ".venv/bin"
        venv.mkdir(parents=True)
        (venv / "python").symlink_to(sys.executable)
        script(venv / "unbake", "#!/bin/sh\n")
        for cmd in ("git init -q", "git add -A -f", "git commit -q -m c"):
            subprocess.run(cmd.split(), cwd=self.repo, check=True, env={**os.environ, **GIT_ENV})
        (self.repo / ".gitignore").write_text(".state/\n.venv/\n")
        decomp = self.tmp / "RageWarsDecomp"
        (decomp / ".git").mkdir(parents=True)
        (decomp / "roms").mkdir()
        (decomp / "roms/baserom.us-rev1.z64").write_bytes(b"rom")
        self.bin = self.tmp / "bin"
        script(self.bin / "cmake", CMAKE)
        script(self.bin / "ccache", "#!/bin/sh\n")
        for compiler in ("gcc-14", "g++-14"):
            script(self.bin / compiler, "#!/bin/sh\n")
        self.calls = self.tmp / "calls.log"

    def build(
        self, *, fail: bool = False, tree: Path | None = None, jobs: str | None = "4"
    ) -> subprocess.CompletedProcess:
        fail_flag = Path(f"{self.calls}.fail")
        fail_flag.unlink(missing_ok=True)
        if fail:
            fail_flag.write_text("")
        env = {**os.environ, **GIT_ENV, "PATH": f"{self.bin}:{os.environ['PATH']}", "CALLS": str(self.calls)}
        env.pop("JOBS", None)
        if jobs is not None:
            env["JOBS"] = jobs
        argv = ["sh", str((tree or self.repo) / "ci/oracle"), "build"]
        return subprocess.run(argv, capture_output=True, text=True, env=env, check=False)

    def test_a_build_without_an_admitted_jobs_refuses_before_any_step(self):
        done = self.build(jobs=None)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("JOBS is unset", done.stderr)
        self.assertEqual(self.called(), [])

    def called(self) -> list[str]:
        return [line.split()[0] for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def test_a_build_after_an_interrupted_one_reruns_no_finished_step(self):
        first = self.build(fail=True)
        self.assertNotEqual(first.returncode, 0)
        self.assertEqual(self.called().count("host-build"), 1)
        for finished in ("deps", "abumas", "recompile"):
            self.assertIn(f"oracle-time: {finished} ", first.stderr)
        self.assertFalse((self.repo / ".state/oracle/live").exists())
        second = self.build()
        self.assertEqual(second.returncode, 0, second.stderr)
        calls = self.called()
        for finished in ("deps", "abumas", "recompile"):
            self.assertEqual(calls.count(finished), 1, f"{finished} reran: {calls}")
            self.assertIn(f"oracle-time: {finished} 0s (finished)", second.stderr)
        self.assertEqual(calls.count("host-build"), 2)
        self.assertIn("readback-hash: ", second.stdout)
        self.assertTrue((self.repo / ".state/oracle/live/n64host").exists())

    def test_a_build_that_is_live_already_runs_nothing(self):
        self.assertEqual(self.build().returncode, 0)
        before = self.called()
        done = self.build()
        self.assertEqual(done.returncode, 0)
        self.assertIn("is live already, build skipped", done.stdout)
        self.assertEqual(self.called(), before)

    def test_a_changed_recompile_input_reruns_the_recompile_but_not_the_deps(self):
        self.assertEqual(self.build().returncode, 0)
        script(self.repo / "src/cake_taste/__main__.py", facts_module("{ }"))
        self.assertEqual(self.build().returncode, 0)
        calls = self.called()
        self.assertEqual((calls.count("deps"), calls.count("abumas"), calls.count("recompile")), (1, 1, 2))

    def test_the_host_build_goes_through_ccache_with_base_dir_and_hash_dir_in_the_launcher(self):
        self.assertEqual(self.build().returncode, 0)
        configure = next(line for line in self.calls.read_text().splitlines() if line.startswith("configure"))
        self.assertIn(f"-DCMAKE_CXX_COMPILER={self.bin}/g++-14", configure)
        self.assertIn(f"-DCMAKE_C_COMPILER={self.bin}/gcc-14", configure)
        self.assertIn(f"-DCMAKE_CXX_COMPILER_LAUNCHER={self.bin}/ccache;base_dir={self.repo};hash_dir=false", configure)
        self.assertIn(f"-DCMAKE_C_COMPILER_LAUNCHER={self.bin}/ccache;base_dir={self.repo};hash_dir=false", configure)
        args = next(self.repo.glob(".state/oracle-recomp/*/args")).read_text()
        self.assertIn(f"{self.repo}/.state/oracle-deps", args)
        self.assertIn(f" us-rev1 {self.tmp / "RageWarsDecomp"}/roms/baserom.us-rev1.z64 ", args)

    def worktree(self, change: str | None = None) -> Path:
        """A second worktree of the local repo at a new commit (so its build is not the live one already);
        CHANGE, when given, is a path under hosts/n64recomp/ it adds: the host key changes, not deps or recompile."""
        tree = self.tmp / "wt"
        env = {**os.environ, **GIT_ENV}
        subprocess.run(["git", "worktree", "add", "-q", "--detach", str(tree)], cwd=self.repo, check=True, env=env)
        (tree / "NOTES").write_text("not an oracle input\n")
        if change:
            (tree / "hosts/n64recomp" / change).write_text("changed\n")
        for cmd in (["git", "add", "-A"], ["git", "commit", "-q", "-m", "w"]):
            subprocess.run(cmd, cwd=tree, check=True, env=env)
        return tree

    def test_a_second_worktree_reuses_the_steps_the_first_finished(self):
        self.assertEqual(self.build().returncode, 0)
        before = self.called()
        tree = self.worktree()
        second = self.build(tree=tree)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.called(), before, "a step ran again in the second worktree")
        for finished in ("deps", "abumas", "recompile", "host-build"):
            self.assertIn(f"oracle-time: {finished} 0s (finished)", second.stderr)
        self.assertFalse((tree / ".state").exists(), "the state must live beside the main working tree")
        self.assertTrue((self.repo / ".state/oracle/live/n64host").exists())
        self.assertEqual((self.repo / ".state/oracle/live").resolve().name, subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=tree, capture_output=True, text=True, check=True).stdout.strip())

    def test_the_step_keys_hold_no_worktree_path(self):
        self.assertEqual(self.build().returncode, 0)
        self.assertEqual(self.build(tree=self.worktree()).returncode, 0)
        for key in (self.repo / ".state/oracle-steps").iterdir():
            self.assertNotIn(str(self.tmp), key.read_text(), key.name)
        self.assertEqual(len(list((self.repo / ".state/oracle-recomp").iterdir())), 1)

    def test_a_host_only_change_in_the_second_worktree_rebuilds_the_host_in_the_shared_dir_and_nothing_before_it(self):
        self.assertEqual(self.build().returncode, 0)
        second = self.build(tree=self.worktree("extra.txt"))
        self.assertEqual(second.returncode, 0, second.stderr)
        calls = self.called()
        self.assertEqual((calls.count("deps"), calls.count("abumas"), calls.count("recompile")), (1, 1, 1))
        self.assertEqual(calls.count("host-build"), 2)
        self.assertNotIn("stale", calls, "cmake met the cache another worktree configured")


if __name__ == "__main__":
    unittest.main()
