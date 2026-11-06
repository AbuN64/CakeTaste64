"""hosts/n64recomp/checkout.sh (deps.sh's per-repository step) on small local repositories with a nested submodule:
a cold and a warm DEST succeed, and so does a rerun after a kill that left a submodule clone half made (no network)."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[1] / "hosts/n64recomp/checkout.sh"
ENV = {
    "PATH": os.environ["PATH"], "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "protocol.file.allow", "GIT_CONFIG_VALUE_0": "always",
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.lan",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.lan",
}


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=ENV, check=False)
    assert done.returncode == 0, (args, done.stderr)
    return done.stdout.strip()


def make_repo(path: Path, files: dict[str, str]) -> str:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    for name, text in files.items():
        (path / name).write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "c")
    return git(path, "rev-parse", "HEAD")


class Checkout(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # runtime (top) -> recomp (submodule) -> toml (nested), like N64ModernRuntime -> N64Recomp -> tomlplusplus
        self.toml = self.tmp / "src/toml"
        make_repo(self.toml, {"toml.h": "1\n"})
        self.recomp = self.tmp / "src/recomp"
        make_repo(self.recomp, {"r.c": "r\n"})
        git(self.recomp, "submodule", "add", "-q", str(self.toml), "lib/toml")
        git(self.recomp, "commit", "-q", "-m", "toml")
        self.runtime = self.tmp / "src/runtime"
        make_repo(self.runtime, {"t.c": "t\n"})
        git(self.runtime, "submodule", "add", "-q", str(self.recomp), "recomp")
        git(self.runtime, "commit", "-q", "-m", "recomp")
        self.pin = git(self.runtime, "rev-parse", "HEAD")
        self.dest = self.tmp / "deps/runtime"
        self.dest.parent.mkdir()

    def checkout(self, pin: str | None = None) -> subprocess.CompletedProcess:
        argv = ["sh", str(CHECKOUT), str(self.dest), str(self.runtime), pin or self.pin]
        return subprocess.run(argv, capture_output=True, text=True, env=ENV, check=False)

    def assert_complete(self):
        self.assertEqual(git(self.dest, "rev-parse", "HEAD"), self.pin)
        self.assertEqual((self.dest / "recomp/lib/toml/toml.h").read_text(), "1\n")

    def break_nested_clone(self):
        """What a kill during the nested submodule clone leaves: git's in-progress HEAD and no working copy."""
        gitdir = self.dest / ".git/modules/recomp/modules/lib/toml"
        self.assertTrue(gitdir.is_dir())
        (gitdir / "HEAD").write_text("ref: refs/heads/.invalid\n")
        for leftover in ("index", "logs", "packed-refs", "refs/heads", "refs/remotes"):
            target = gitdir / leftover
            shutil.rmtree(target) if target.is_dir() else target.unlink(missing_ok=True)
        workdir = self.dest / "recomp/lib/toml"
        for entry in workdir.iterdir():
            if entry.name != ".git":
                shutil.rmtree(entry) if entry.is_dir() else entry.unlink()

    def test_a_cold_dest_gets_the_nested_submodule_at_its_gitlink(self):
        done = self.checkout()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assert_complete()

    def test_a_warm_dest_succeeds_again(self):
        self.assertEqual(self.checkout().returncode, 0)
        done = self.checkout()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assert_complete()

    def test_the_unrepaired_update_fails_on_the_half_made_clone(self):  # the cause of "Unable to find current revision"
        self.assertEqual(self.checkout().returncode, 0)
        self.break_nested_clone()
        plain = subprocess.run(["git", "-C", str(self.dest), "submodule", "update", "--init", "--recursive"],
                               capture_output=True, text=True, env=ENV, check=False)
        self.assertNotEqual(plain.returncode, 0)
        self.assertIn("Unable to find current revision in submodule path", plain.stderr)

    def test_a_rerun_after_a_kill_in_a_nested_submodule_clone_succeeds(self):
        self.assertEqual(self.checkout().returncode, 0)
        self.break_nested_clone()
        done = self.checkout()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assert_complete()

    def test_a_rerun_after_a_kill_in_a_submodule_clone_with_no_working_copy_gitfile_succeeds(self):
        self.assertEqual(self.checkout().returncode, 0)
        self.break_nested_clone()
        (self.dest / "recomp/lib/toml/.git").unlink()
        done = self.checkout()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assert_complete()

    def test_a_half_made_main_clone_beside_dest_is_replaced(self):
        (self.dest.parent / "runtime.clone").mkdir()
        (self.dest.parent / "runtime.clone/junk").write_text("x")
        done = self.checkout()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assert_complete()
        self.assertFalse((self.dest.parent / "runtime.clone").exists())

    def test_a_dest_with_edits_off_the_pin_is_refused(self):
        self.assertEqual(self.checkout().returncode, 0)
        (self.runtime / "t.c").write_text("t2\n")
        git(self.runtime, "commit", "-q", "-am", "next")
        (self.dest / "t.c").write_text("edited\n")
        done = self.checkout(git(self.runtime, "rev-parse", "HEAD"))
        self.assertEqual(done.returncode, 1)
        self.assertIn("has edits and is not at", done.stderr)


if __name__ == "__main__":
    unittest.main()
