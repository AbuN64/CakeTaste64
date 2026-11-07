"""The golden store: one oracle record per (ROM sha1, oracle key, movie sha256); a repeat never runs the oracle."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cake_taste import TasteError
from cake_taste.oracle import store

SLICE = (
    Path(__file__).resolve().parent / "fixtures" / "oracle_record_slice.jsonl"
)  # the head of a real boot-idle record
PARAMS = {"pak": "on", "pads": 1, "exit_after": 300, "calls": 8}


class Store(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        live = self.root / ".state" / "oracle" / "live"
        live.mkdir(parents=True)
        (live / "n64host").write_text("host")
        rom = self.root / "rom.z64"
        rom.write_bytes(b"cartridge")
        self.layout = store.Layout(live / "n64host", "keyA", rom, self.root / ".state" / "oracle-records")
        self.movie = self.root / "movie.raw"
        self.movie.write_text("0000 0 0\n" * 3)
        self.runs: list[dict] = []
        self.driver = self.root / "vulkan/icd.d/lvp.arch.json"
        discovery = patch.object(store.icd, "discover", return_value=self.driver)
        self.discovery = discovery.start()
        self.addCleanup(discovery.stop)

    def runner(self, code=0, body=None):
        def run(argv, env, cwd):
            self.runs.append(dict(env))
            if body is not False:
                (cwd / store.RECORD).write_bytes(SLICE.read_bytes() if body is None else body)
            return code

        return run

    def test_repeat_is_served_without_running_the_oracle(self):
        first = store.record(self.layout, self.movie, PARAMS, self.root / "a", self.runner())
        again = store.record(self.layout, self.movie, PARAMS, self.root / "b", self.runner(code=9))
        self.assertFalse(first["served"])
        self.assertTrue(again["served"])
        self.assertEqual(len(self.runs), 1)
        self.assertEqual((self.root / "b" / store.RECORD).read_bytes(), SLICE.read_bytes())
        self.assertEqual(first["path"], again["path"])
        self.assertEqual(again["bytes"], SLICE.stat().st_size)
        self.assertIn(first["rom_sha1"] + "/keyA/" + first["movie_sha256"], first["path"])

    def test_oracle_build_or_movie_change_records_again(self):
        store.record(self.layout, self.movie, PARAMS, None, self.runner())
        store.record(
            store.Layout(self.layout.host, "keyB", self.layout.rom, self.layout.store),
            self.movie,
            PARAMS,
            None,
            self.runner(),
        )
        self.movie.write_text("0000 0 0\n" * 4)
        store.record(self.layout, self.movie, PARAMS, None, self.runner())
        self.assertEqual(len(self.runs), 3)

    def test_other_params_at_a_stored_address_are_refused(self):
        store.record(self.layout, self.movie, PARAMS, None, self.runner())
        with self.assertRaises(TasteError) as raised:
            store.record(self.layout, self.movie, {**PARAMS, "exit_after": 5}, None, self.runner())
        self.assertEqual(raised.exception.key, "oracle.params")

    def test_a_mask_is_its_own_address_and_reaches_the_host(self):
        plain = store.record(self.layout, self.movie, PARAMS, None, self.runner())
        masked = store.record(self.layout, self.movie, {**PARAMS, "mask": "800e9400-800ebc00"}, None, self.runner())
        again = store.record(self.layout, self.movie, {**PARAMS, "mask": "800e9400-800ebc00"}, None, self.runner(9))
        other = store.record(self.layout, self.movie, {**PARAMS, "mask": "800e9400-800ebc04"}, None, self.runner())
        self.assertEqual([r["served"] for r in (plain, masked, again, other)], [False, False, True, False])
        self.assertEqual(len({plain["path"], masked["path"], other["path"]}), 3)
        self.assertNotIn("N64R_LOCKSTEP_MASK", self.runs[0])
        self.assertEqual(self.runs[1]["N64R_LOCKSTEP_MASK"], "800e9400-800ebc00")
        self.assertEqual(masked["mask_sha256"], store.mask_sha256("800e9400-800ebc00"))
        self.assertNotIn("mask_sha256", plain)

    def test_a_record_stored_before_the_mask_is_served_unmasked(self):
        final = store.folder(self.layout, store.sha1_file(self.layout.rom), store.sha256_file(self.movie))
        final.mkdir(parents=True)
        (final / store.RECORD).write_bytes(SLICE.read_bytes())
        (final / store.FACTS).write_text(json.dumps({"params": PARAMS, "bytes": 1, "seconds": 1}))
        self.assertTrue(store.record(self.layout, self.movie, PARAMS, None, self.runner(9))["served"])
        masked = store.record(self.layout, self.movie, {**PARAMS, "mask": "0-4"}, None, self.runner())
        self.assertFalse(masked["served"])

    def test_failed_or_empty_oracle_stores_nothing(self):
        for runner, key in (
            (self.runner(code=3), "oracle.failed"),
            (self.runner(body=b""), "oracle.empty"),
            (self.runner(body=False), "oracle.empty"),
        ):
            with self.assertRaises(TasteError) as raised:
                store.record(self.layout, self.movie, PARAMS, None, runner)
            self.assertEqual(raised.exception.key, key)
        self.assertEqual([p.name for p in self.layout.store.iterdir()], [])

    def test_host_env_is_the_selfcheck_environment(self):
        store.record(self.layout, self.movie, PARAMS, None, self.runner())
        env = self.runs[0]
        self.assertEqual(env["N64R_TAS_PLAY"], str(self.movie))
        self.assertEqual((env["N64R_TAS_EXIT_AFTER"], env["N64R_PADS"], env["N64R_LOCKSTEP_CALLS"]), ("300", "1", "8"))
        self.assertEqual(env["N64R_CONFIG"], str(self.root / ".state" / "cfg"))
        self.assertEqual(env["VK_DRIVER_FILES"], str(self.driver))

    def test_missing_icd_refuses_before_running_the_host(self):
        self.discovery.side_effect = FileNotFoundError("install the lavapipe Vulkan driver")
        with self.assertRaises(TasteError) as raised:
            store.record(self.layout, self.movie, PARAMS, None, self.runner())
        self.assertEqual(raised.exception.key, "oracle.icd")
        self.assertIn("install the lavapipe", raised.exception.detail)
        self.assertEqual(self.runs, [])

    def test_real_record_slice_shape(self):
        lines = [json.loads(line) for line in SLICE.read_text().splitlines()]
        self.assertEqual((lines[0]["k"], lines[0]["side"]), ("meta", "oracle"))
        polls = [x["poll"] for x in lines if x["k"] == "poll"]
        self.assertEqual(polls, sorted(polls))
        self.assertTrue(polls)


if __name__ == "__main__":
    unittest.main()
