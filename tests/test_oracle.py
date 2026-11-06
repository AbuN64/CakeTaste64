"""ci/oracle and the scripts it calls: every path is found from the repository, none from the environment."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORACLE = ROOT / "ci/oracle"
SCRIPTS = (ORACLE, ROOT / "hosts/n64recomp/recompile.sh", ROOT / "hosts/n64recomp/decomp_elf.sh")


def git(cwd: Path, *args: str) -> None:
    env = {
        "PATH": os.environ["PATH"],
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.lan",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.lan",
    }
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, env=env)


class Shape(unittest.TestCase):
    def test_the_scripts_parse_and_ci_oracle_is_executable(self):
        self.assertTrue(ORACLE.stat().st_mode & 0o100)
        for script in SCRIPTS:
            self.assertEqual(subprocess.run(["sh", "-n", str(script)], check=False).returncode, 0, script)

    def test_no_script_reads_a_home_path_a_site_url_or_an_environment_input(self):
        for script in SCRIPTS:
            text = script.read_text()
            for literal in (
                "$HOME",
                "~/",
                "/home/",
                "rig-state",
                "ORACLE_DECOMP",
                "ORACLE_DEPS",
                "RAGEWARS_LOCAL",
                "TOOLKIT",
                "http://",
                "JOBS:?",
            ):
                self.assertNotIn(literal, text, f"{script.name}: {literal}")

    def test_the_decomp_is_the_sibling_repo_and_the_state_is_beside_the_main_working_tree(self):
        text = ORACLE.read_text()
        self.assertIn("git rev-parse --path-format=absolute --git-common-dir", text)
        self.assertIn('decomp=$(dirname "$main")/RageWarsDecomp', text)
        self.assertIn("state=$main/.state", text)
        for name in ("taste-profile.json", "oracle-facts.json", "oracle-deps", "oracle/live"):
            self.assertIn(name, text)
        self.assertIn("/.state/", (ROOT / ".gitignore").read_text().splitlines())

    def test_unbake_comes_from_the_declared_toolchain_not_the_path(self):
        text = ORACLE.read_text()
        self.assertIn("upy=$(python3 ci/ensure_venv.py unbake)", text)
        self.assertIn('unbake=$(dirname "$upy")/unbake', text)
        self.assertIn("py=$(python3 ci/ensure_venv.py)", text)
        self.assertIn('"$unbake" --host "$host" setup --no-make', (ROOT / "hosts/n64recomp/decomp_elf.sh").read_text())
        self.assertIn('oracle = ["unbake"]', (ROOT / "pyproject.toml").read_text())


class Refusals(unittest.TestCase):
    def tree(self) -> Path:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "repo/ci").mkdir(parents=True)
        shutil.copy(ORACLE, tmp / "repo/ci/oracle")
        git(tmp / "repo", "init", "-q")
        return tmp / "repo"

    def run_oracle(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        env = {"PATH": os.environ["PATH"]}
        argv = ["sh", str(repo / "ci/oracle"), *args]
        return subprocess.run(argv, capture_output=True, text=True, env=env, check=False)

    def test_the_selfcheck_refuses_a_host_that_exits_0_without_writing_the_state_hash(self):
        """A host stopped by a signal can exit 0 with state-oracle.txt empty; two empty files hash equal and
        passed the check."""
        repo = self.tree()
        decomp = repo.parent / "RageWarsDecomp/roms"
        decomp.mkdir(parents=True)
        (decomp / "baserom.us-rev1.z64").write_bytes(b"rom")
        host = repo.parent / "build/n64host"
        (host.parent / "gen").mkdir(parents=True)
        (host.parent / "gen/symbol_addrs.txt").write_text("gMain = 0x80001000; // type:func\n")
        host.write_text("#!/bin/sh\nexit 0\n")
        host.chmod(0o755)
        done = self.run_oracle(repo, "selfcheck", str(host))
        self.assertEqual(done.returncode, 1)
        self.assertIn("wrote no state hash", done.stderr)
        self.assertNotIn("readback-hash", done.stdout)

    def test_a_missing_sibling_decomp_is_refused_with_its_next_step(self):
        done = self.run_oracle(self.tree(), "build")
        self.assertEqual(done.returncode, 1)
        self.assertIn("RageWarsDecomp is missing; clone RageWarsDecomp beside this repository", done.stderr)

    def test_the_decomp_is_found_beside_the_main_working_tree_for_a_checkout_and_for_a_worktree(self):
        repo = self.tree()
        git(repo, "add", "ci/oracle")
        git(repo, "commit", "-q", "-m", "c")
        worktree = repo.parent / "runs/card/repo"
        git(repo, "worktree", "add", "-q", "-b", "card", str(worktree))
        for tree in (repo, worktree):
            done = self.run_oracle(tree, "build")
            self.assertEqual(done.returncode, 1)
            self.assertIn(f"the decomp working copy {repo.parent}/RageWarsDecomp is missing", done.stderr)
        (repo.parent / "RageWarsDecomp/.git").mkdir(parents=True)
        for tree in (repo, worktree):
            self.assertIn(
                f"{repo.parent}/RageWarsDecomp/roms/baserom.us-rev1.z64 is missing",
                self.run_oracle(tree, "build").stderr,
            )

    def test_a_missing_rom_in_the_sibling_is_refused_naming_the_file(self):
        repo = self.tree()
        (repo.parent / "RageWarsDecomp/.git").mkdir(parents=True)
        done = self.run_oracle(repo, "build")
        self.assertEqual(done.returncode, 1)
        self.assertIn("RageWarsDecomp/roms/baserom.us-rev1.z64 is missing", done.stderr)

    def test_the_selfcheck_names_a_host_that_is_not_there(self):
        done = self.run_oracle(self.tree(), "selfcheck")
        self.assertEqual(done.returncode, 1)
        self.assertIn("selfcheck: ", done.stderr)
        self.assertIn("is missing", done.stderr)

    def test_an_unknown_command_prints_the_usage(self):
        self.assertEqual(self.run_oracle(self.tree(), "nope").returncode, 2)


if __name__ == "__main__":
    unittest.main()
