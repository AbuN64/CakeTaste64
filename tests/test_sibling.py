"""Sibling repositories (RageWarsDecomp, RageWarsEnhanced) are found beside the repository's own top level, never
beside the registered checkout that a worktree's git common dir points at."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from cake_taste import TasteError
from cake_taste.config import ConfigError, load_profile, sibling
from cake_taste.oracle import store


def git(path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.lan", "-C", str(path), *args],
        capture_output=True,
        check=True,
    )


class SiblingTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.registered = root / "registered" / "CakeTaste64"
        (self.registered / "hosts").mkdir(parents=True)
        profile = {
            "schema_version": 1,
            "name": "p",
            "project_root": "../../RageWarsDecomp",
            "runtime_root": "../.state/run",
            "output_root": "../.state/run",
            "lockstep": {"map": "../../RageWarsEnhanced/map.json"},
        }
        (self.registered / "hosts" / "p.json").write_text(json.dumps(profile))
        git(self.registered, "init", "-q")
        git(self.registered, "add", ".")
        git(self.registered, "commit", "-q", "-m", "profile")
        self.run_tree = root / "run" / "CakeTaste64"
        git(self.registered, "worktree", "add", "-q", "-b", "card", str(self.run_tree))

    def test_a_worktree_finds_the_sibling_beside_its_own_top_level(self) -> None:
        self.assertEqual(sibling(self.run_tree, "RageWarsDecomp"), self.run_tree.parent / "RageWarsDecomp")
        self.assertEqual(sibling(self.registered, "RageWarsDecomp"), self.registered.parent / "RageWarsDecomp")

    def test_profile_sibling_paths_resolve_beside_the_worktree_and_the_cache_stays_at_the_main_tree(self) -> None:
        (self.registered.parent / "RageWarsDecomp").mkdir()  # beside the registered checkout only: not used
        with self.assertRaises(ConfigError) as raised:
            load_profile(self.run_tree / "hosts" / "p.json")
        self.assertIn(f"directory does not exist: {self.run_tree.parent / 'RageWarsDecomp'}", str(raised.exception))
        (self.run_tree.parent / "RageWarsDecomp").mkdir()
        loaded = load_profile(self.run_tree / "hosts" / "p.json")
        self.assertEqual(loaded.project_root, self.run_tree.parent / "RageWarsDecomp")
        self.assertEqual(loaded.resolve_path("../../RageWarsEnhanced/x"), self.run_tree.parent / "RageWarsEnhanced/x")
        self.assertEqual(loaded.runtime_root, self.registered / ".state" / "run")

    def test_sibling_outside_a_git_repository_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as bare, self.assertRaises(TasteError):
            sibling(Path(bare), "RageWarsDecomp")

    def test_discover_uses_the_sibling_beside_the_worktree_and_refuses_one_missing_there(self) -> None:
        live = self.registered / ".state/oracle/live"
        live.mkdir(parents=True)
        (live / "n64host").write_bytes(b"h")
        (live / "key").write_text("k\n")
        rom = Path("RageWarsDecomp/roms/baserom.us-rev1.z64")
        (self.registered.parent / rom).parent.mkdir(parents=True)
        (self.registered.parent / rom).write_bytes(b"registered")  # beside the registered checkout only: not used
        with self.assertRaises(TasteError) as raised:
            store.discover(self.run_tree)
        self.assertEqual(raised.exception.key, "oracle.missing")
        self.assertIn(str(self.run_tree.parent / rom), str(raised.exception))
        (self.run_tree.parent / rom).parent.mkdir(parents=True)
        (self.run_tree.parent / rom).write_bytes(b"own")
        found = store.discover(self.run_tree)
        self.assertEqual(found.rom, self.run_tree.parent / rom)
        self.assertEqual(found.host, live / "n64host")  # the per-host cache stays at the main working tree
        self.assertEqual(found.store, self.registered / ".state" / "oracle-records")


if __name__ == "__main__":
    unittest.main()
