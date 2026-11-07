"""A path has one identity at every reader: the modules that compare temp paths pass under a symlinked TMPDIR."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = ["test_config", "test_oracle", "test_oracle_inputs", "test_oracle_resume"]


class SymlinkedTmpdir(unittest.TestCase):
    def test_the_path_comparing_modules_pass_under_a_symlinked_tmpdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp).resolve() / "real"
            real.mkdir()
            link = Path(tmp).resolve() / "link"
            link.symlink_to(real)
            env = dict(os.environ, TMPDIR=str(link))
            done = subprocess.run(
                [sys.executable, "-m", "unittest", *MODULES],
                cwd=ROOT / "tests", env=env, capture_output=True, text=True,
            )
            self.assertEqual(done.returncode, 0, done.stderr[-3000:])


if __name__ == "__main__":
    unittest.main()
