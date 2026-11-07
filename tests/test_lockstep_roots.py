"""Lockstep roots: onsets per locus, episodes by the links the record shows, root/provisional/unresolved, the judge."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from cake_taste import TasteError
from cake_taste.cli import main
from cake_taste.lockstep import roots

# The head of a real census (tests/fixtures/lockstep_match_slice.json names its source).
SLICE = Path(__file__).resolve().parent / "fixtures" / "lockstep_match_slice.json"
MAP = {
    "version": 1,
    "classes": [
        {"name": "input", "locus": []},
        {"name": "vi", "locus": []},
        {"name": "clock", "locus": []},
        {"name": "calls", "locus": ["thread", "caller", "fn"], "thread": "thread", "within": "caller", "unit": "fn"},
        {"name": "memory", "locus": ["address"], "unresolved": {"class": "page"}},
    ],
    "gap": 60,
    "coincide": 1,
    "margin": 30,
    "covered": "polls.common",
}
SPEC = roots.parse_map(MAP)


def call(poll, thread, caller, fn, field="poll", **extra):
    return {"poll": poll, "thread": thread, "caller": caller, "fn": fn, "field": field, **extra}


def report(**classes):
    return {"polls": {"common": 100}, "divergences": classes}


class Census(unittest.TestCase):
    def test_a_locus_is_one_onset_however_many_rows(self):
        found = roots.onsets(report(clock=[{"poll": 9}, {"poll": 7}, {"poll": 12}]), SPEC)
        self.assertEqual([(o.id, o.rows, o.last) for o in found], [("clock@7", 3, 12)])

    def test_nesting_and_thread_carry_and_the_upstream_class_roots_one_poll(self):
        out = roots.census(
            report(
                calls=[
                    call(10, "T", "W", "Read"),
                    call(10, "T", "Read", "Lock"),  # nested in a diverged unit of T
                    call(40, "T", "W", "Other"),  # T again within the gap
                    call(10, "U", "X", "Y"),  # another thread, same poll: coincides
                ],
                clock=[{"poll": 10}],
            ),
            SPEC,
        )
        self.assertEqual(out["episodes"], 1)
        (episode,) = out["roots"]
        self.assertEqual((episode["id"], episode["status"], episode["through"]), ("clock@10", "root", 40))
        links = {m["id"]: m["link"] for m in episode["members"]}
        self.assertEqual(links["calls@10:T/W/Read"], "nesting")  # it encloses Lock, which came first
        self.assertEqual(links["calls@40:T/W/Other"], "thread")
        self.assertEqual(links["calls@10:U/X/Y"], "coincide")

    def test_a_later_onset_while_another_is_active_is_provisional(self):
        out = roots.census(
            report(calls=[call(10, "T", "W", "Read"), call(50, "T", "W", "Read"), call(30, "U", "X", "Y")]), SPEC
        )
        state = {e["id"]: (e["status"], e["upstream"]) for e in out["roots"]}
        self.assertEqual(state["calls@10:T/W/Read"], ("root", []))
        self.assertEqual(state["calls@30:U/X/Y"], ("provisional", ["calls@10:T/W/Read"]))

    def test_past_the_gap_nothing_carries(self):
        out = roots.census(report(calls=[call(10, "T", "W", "Read"), call(200, "T", "W", "Other")]), SPEC)
        self.assertEqual([e["status"] for e in out["roots"]], ["root", "root"])

    def test_unknown_resolution_is_its_own_episode_and_never_makes_a_root_provisional(self):
        page = {"poll": 5, "address": "80001000", "class": "page"}
        out = roots.census(
            report(memory=[page, dict(page, address="80002000", poll=6)], calls=[call(5, "T", "W", "R")]), SPEC
        )
        state = {e["id"]: (e["status"], e["onsets"], e["unresolved_upstream"]) for e in out["roots"]}
        self.assertEqual(state["calls@5:T/W/R"], ("root", 1, []))
        self.assertEqual(state["memory@5:80001000"], ("unresolved", 2, []))
        self.assertEqual(out["dump_polls"], [5])

    def test_the_real_head_reduces_to_its_episodes(self):
        out = roots.census(json.loads(SLICE.read_text()), SPEC)
        self.assertEqual(out["covered"], 59584)
        resolved = [(e["id"], e["status"]) for e in out["roots"] if e["status"] != "unresolved"]
        self.assertEqual(
            resolved[:2],
            [
                ("calls@2275:Vhandler_RunPageLoadWorker/Cartridge_AcquireReadRequest/List_PrependIntrusive", "root"),
                (
                    "calls@2287:Audiomgr_RunAudioWorker/Cartridge_AcquireReadRequest/List_PrependIntrusive",
                    "provisional",
                ),
            ],
        )
        self.assertIn(("clock@13885", "root"), resolved)  # the head ends at 2300: nothing is active at 13885
        first = out["roots"][0]
        self.assertEqual((first["id"], first["status"], first["onsets"]), ("memory@1:800c7000", "unresolved", 12))
        self.assertLess(out["episodes"], out["onsets"])

    def test_bad_input_is_refused_by_name(self):
        for bad in ({}, report(calls=[{"fn": "x"}]), report(audio=[])):
            with self.assertRaises(TasteError) as caught:
                roots.census(bad, SPEC)
            self.assertEqual(caught.exception.key, "lockstep.report")
        for bad_map in ({**MAP, "version": 2}, {**MAP, "classes": []}, {**MAP, "extra": 1},
                        {**MAP, "classes": [{"name": "c", "within": "caller"}]}):  # fmt: skip
            with self.assertRaises(TasteError) as caught:
                roots.parse_map(bad_map)
            self.assertEqual(caught.exception.key, "lockstep.map")


class Verify(unittest.TestCase):
    def setUp(self):
        self.kept = roots.census(
            report(calls=[call(10, "T", "W", "Read"), call(80, "U", "X", "Y")], clock=[{"poll": 300}]), SPEC
        )

    def test_gone_present_moved_beyond_and_new(self):
        now = report(calls=[call(80, "U", "X", "Y"), call(20, "V", "P", "Q")], clock=[{"poll": 300}])
        now["polls"]["common"] = 120
        now["divergences"]["calls"].append(call(90, "T", "W", "Read"))
        judged = roots.verify(now, SPEC, self.kept)
        states = {r["id"]: r["state"] for r in judged["roots"]}
        self.assertEqual(states, {"calls@10:T/W/Read": "moved", "calls@80:U/X/Y": "present", "clock@300": "beyond"})
        self.assertEqual([n["id"] for n in judged["new"]], ["calls@20:V/P/Q"])

    def test_named_roots_only_and_unknown_names_refused(self):
        judged = roots.verify(report(), SPEC, self.kept, ["calls@10:T/W/Read"])
        self.assertEqual([(r["id"], r["state"]) for r in judged["roots"]], [("calls@10:T/W/Read", "gone")])
        with self.assertRaises(TasteError) as caught:
            roots.verify(report(), SPEC, self.kept, ["calls@11:T/W/Read"])
        self.assertEqual(caught.exception.key, "lockstep.root")


class Command(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "source").mkdir()
        (self.root / "map.json").write_text(json.dumps(MAP))
        self.profile = self.root / "profile.json"
        self.profile.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "name": "t",
                    "project_root": "source",
                    "runtime_root": "state",
                    "output_root": "evidence",
                    "lockstep": {"map": "map.json"},
                }
            )
        )

    def taste(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--profile", str(self.profile), "lockstep", *argv])
        return code, json.loads(out.getvalue())

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value))
        return str(self.root / name)

    def test_roots_then_the_judge_of_a_fix(self):
        before = self.write("before.json", report(calls=[call(10, "T", "W", "Read")], clock=[{"poll": 50}]))
        kept = str(self.root / "kept.json")
        code, envelope = self.taste("roots", "--match", before, "--out", kept, "--members", "0")
        self.assertEqual(code, 0)
        self.assertEqual([e["id"] for e in envelope["result"]["roots"]], ["calls@10:T/W/Read", "clock@50"])
        fixed = self.write("fixed.json", report(clock=[{"poll": 50}]))
        code, envelope = self.taste("roots", "--match", fixed, "--against", kept, "--root", "calls@10:T/W/Read")
        self.assertEqual(code, 0, envelope["findings"])
        code, envelope = self.taste("roots", "--match", before, "--against", kept, "--root", "calls@10:T/W/Read")
        self.assertEqual((code, envelope["findings"][0]["key"]), (1, "lockstep.present"))
        early = self.write("early.json", report(calls=[call(5, "Z", "A", "B")]))
        code, envelope = self.taste("roots", "--match", early, "--against", kept, "--root", "calls@10:T/W/Read")
        self.assertEqual((code, envelope["findings"][0]["key"]), (1, "lockstep.new"))
        self.assertTrue(json.loads(Path(kept).read_text())["roots"][0]["id"])

    def test_root_without_against_is_refused(self):
        code, envelope = self.taste("roots", "--match", self.write("m.json", report()), "--root", "x")
        self.assertEqual((code, envelope["findings"][0]["key"]), (1, "cli.arguments"))


if __name__ == "__main__":
    unittest.main()
