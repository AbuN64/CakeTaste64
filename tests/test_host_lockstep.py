import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / "hosts" / "n64recomp"
DRIVER = r"""
#include <cstdint>
#include <cstring>
extern "C" uint64_t n64host_golden_frame() { return 42; }
// main.cpp's table
extern "C" uint32_t n64host_replaced_vram(size_t i) { return i < 2 ? 0x802bef68u + 0x104u * (uint32_t)i : 0u; }
// the retrace count: never a call's frame
extern "C" { volatile unsigned long long n64host_tas_poll = 5; }  // golden.cpp: the polls read so far
extern "C" int n64host_lockstep_on;
extern "C" void n64host_lockstep_entry(const char *, const uint8_t *, uint32_t, uint32_t, uint32_t, uint32_t,
                                       uint64_t, uint64_t, uint32_t);
extern "C" void n64host_lockstep_return(const char *, const uint8_t *, uint32_t, uint32_t, uint64_t, uint32_t);
static uint8_t rdram[0x4000];
static const char f[] = "f", g[] = "g", h[] = "h";
int main() {
    if (!n64host_lockstep_on) return 3;
    uint32_t w = 0x11223344;                       // host-order word: canonical bytes 11 22 33 44
    std::memcpy(rdram + 0x2000, &w, 4);
    uint32_t ptr = 0x80001000;                     // a pointer word inside the window h's a0 names
    std::memcpy(rdram + 0x2004, &ptr, 4);
    n64host_lockstep_entry(f, rdram, 1, 2, 3, 4, 0x3ff8000000000000ull, 0, 0x80003f00);
    rdram[0x1000 ^ 3] = 0xAB;                      // byte store at 0x80001000 through the ^3 layout
    n64host_lockstep_entry(g, rdram, 5, 0, 0, 0, 0, 0, 0x80003e00);    // g at f's depth + 1
    n64host_lockstep_entry(h, rdram, 0x80002000, 0, 0, 0, 0, 0, 0x80003e00);    // h tail-called from g: same sp
    n64host_lockstep_return(h, rdram, 7, 0, 0, 0x80003e00);
    n64host_lockstep_return(f, rdram, 9, 8, 0x4000000000000000ull, 0x80003f00);  // g's frame is dropped
    for (int i = 0; i < 10; i++) n64host_lockstep_entry(f, rdram, (uint32_t)i, 0, 0, 0, 0, 0, 0x80003f00);
    return 0;
}
"""

# A 3-poll movie fixture: two controllers, a hit between polls (the poll stream keeps its own page baseline).
POLL_DRIVER = r"""
#include <cstdint>
#include <cstring>
extern "C" uint64_t n64host_golden_frame() { return 7; }
// main.cpp's table
extern "C" uint32_t n64host_replaced_vram(size_t i) { return i < 2 ? 0x802bef68u + 0x104u * (uint32_t)i : 0u; }
extern "C" { volatile unsigned long long n64host_tas_poll = 0; }
extern "C" int n64host_lockstep_on;
extern "C" void n64host_lockstep_entry(const char *, const uint8_t *, uint32_t, uint32_t, uint32_t, uint32_t,
                                       uint64_t, uint64_t, uint32_t);
extern "C" void n64host_lockstep_poll(uint64_t, uint64_t, uint64_t, const uint8_t *, const uint32_t *, unsigned);
static uint8_t rdram[0x4000];
int main() {
    if (!n64host_lockstep_on) return 3;
    const uint32_t pads[3][2] = {{0x00000000, 0x80000000}, {0x900050b0, 0x00000000}, {0x0010ff01, 0x00200000}};
    uint32_t w = 0x11223344;
    std::memcpy(rdram + 0x2000, &w, 4);
    n64host_tas_poll = 1;                          // golden.cpp sets it before the poll line, as here
    n64host_lockstep_poll(1, 2, 16683333, rdram, pads[0], 2);
    rdram[0x1000 ^ 3] = 0xAB;                      // changes page 1 before the hit
    n64host_lockstep_entry("f", rdram, 0, 0, 0, 0, 0, 0, 0x80003f00);
    n64host_tas_poll = 2;
    n64host_lockstep_poll(2, 4, 33366666, rdram, pads[1], 2);
    n64host_tas_poll = 3;
    n64host_lockstep_poll(3, 6, 50049999, rdram, pads[2], 2);  // nothing changed
    return 0;
}
"""


def fnv(data):
    h = 0xCBF29CE484222325
    for (w,) in struct.iter_unpack("<Q", data):
        h = ((h ^ w) * 0x100000001B3) & ((1 << 64) - 1)
    return h


class LockstepDocsTest(unittest.TestCase):
    def test_every_lockstep_env_var_is_documented(self):
        source = (HOST / "lockstep.cpp").read_text(encoding="utf-8")
        names = set(re.findall(r"getenv\(\"(N64R_[A-Z_]+)\"\)", source))
        self.assertEqual(names, {"N64R_LOCKSTEP_DIR", "N64R_LOCKSTEP_RAM", "N64R_LOCKSTEP_CALLS",
                                 "N64R_LOCKSTEP_WATCH", "N64R_LOCKSTEP_DUMP", "N64R_LOCKSTEP_CALLS_POLLS",
                                 "N64R_LOCKSTEP_MASK"})
        docs = (HOST / "RUNTIME.md").read_text(encoding="utf-8")
        for name in names:
            self.assertIn(f"`{name}`", docs)

    def test_recorder_is_inert_without_the_directory(self):
        source = (HOST / "lockstep.cpp").read_text(encoding="utf-8")
        self.assertIn("if (!dir || !*dir) return 0;", source)
        probe = (HOST / "probe" / "trace.h").read_text(encoding="utf-8")
        self.assertIn("if (n64host_lockstep_on) n64host_lockstep_entry(", probe)
        self.assertIn("if (n64host_lockstep_on) n64host_lockstep_return(", probe)
        self.assertIn("lockstep.cpp", (HOST / "CMakeLists.txt").read_text(encoding="utf-8"))
        golden = (HOST / "golden.cpp").read_text(encoding="utf-8")
        self.assertIn("if (n64host_lockstep_on) {", golden)
        self.assertIn("n64host_lockstep_poll(polls, vi_count.load(), n64host_vclock_ns(), n64host_rdram, words, "
                      "n64host_golden_pads());", golden)


@unittest.skipUnless(shutil.which("g++"), "needs g++")
class LockstepRecorderTest(unittest.TestCase):
    def test_points_calls_returns_in_canonical_byte_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "driver.cpp").write_text(DRIVER)
            exe = path / "driver"
            subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", "-Werror", str(HOST / "lockstep.cpp"),
                            str(path / "driver.cpp"), "-o", str(exe), "-lpthread"], check=True, capture_output=True,
                           text=True)
            out = path / "ls"
            env = dict(os.environ, N64R_LOCKSTEP_DIR=str(out), N64R_LOCKSTEP_RAM="0x4000",
                       N64R_LOCKSTEP_WATCH="0x80001000@5,0x80002000@6-9,0x80002004")
            result = subprocess.run([str(exe)], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = [json.loads(x) for x in (out / "lockstep.jsonl").read_text().splitlines()]
            self.assertEqual(lines[0]["side"], "oracle")
            self.assertEqual(lines[0]["bytes"], "canonical-be")
            # the runtime replacements main.cpp registers: none of their calls can be recorded, so the meta names them
            self.assertEqual(lines[0]["replaced"], ["802bef68", "802bf06c"])
            hits = [x for x in lines if x["k"] == "hit"]
            self.assertEqual([x["fn"] for x in hits], ["f", "g", "h"])
            self.assertEqual(len(hits[0]["pages"]), 4)
            self.assertEqual([p[0] for p in hits[1]["pages"]], [1])
            self.assertEqual(hits[2]["pages"], [])
            self.assertEqual(hits[0]["vi"], 42)
            # the record is hashes only: the canonical bytes hash to what the hits list, and no page bytes are kept
            page2 = bytes.fromhex("11223344") + struct.pack(">I", 0x80001000) + bytes(4096 - 8)
            self.assertEqual(int(hits[0]["pages"][2][1], 16), fnv(page2))
            self.assertEqual(int(hits[1]["pages"][0][1], 16), fnv(b"\xab" + bytes(4095)))
            self.assertEqual(sorted(p.name for p in out.iterdir()), ["lockstep.jsonl"])
            calls = [x for x in lines if x["k"] == "call"]
            # the first 8 calls each caller makes of a function, then the power-of-two ordinals (8),
            # whatever the call number
            self.assertEqual([(x["fn"], x["i"]) for x in calls],
                             [("f", 0), ("g", 0), ("h", 0)] + [("f", i) for i in range(1, 11)])
            self.assertEqual([(x["fn"], x["caller"], x["o"]) for x in calls],
                             [("f", "?", 0), ("g", "f", 0), ("h", "g", 0), ("f", "?", 1)] +
                             [("f", "f", o) for o in range(9)])
            self.assertEqual({x["frame"] for x in calls}, {5})  # the poll index, as the port stamps; not retrace 42
            self.assertEqual((lines[0]["frame"], lines[0]["calls"]), ("poll", 8))
            self.assertEqual(calls[0]["a"], ["00000001", "00000002", "00000003", "00000004"])
            self.assertEqual(calls[0]["f12"], "3ff8000000000000")
            rets = [x for x in lines if x["k"] == "ret"]
            self.assertEqual([(x["fn"], x["i"], x["r"]) for x in rets],
                             [("h", 0, ["00000007", "00000000"]), ("f", 0, ["00000009", "00000008"])])
            self.assertEqual(rets[1]["f0"], "4000000000000000")
            # the memory a0 points at, read at the return: guest words, an in-RAM address as pppppppp
            self.assertEqual(rets[0]["m"], {"0": "11223344" + "pppppppp" + "00000000" * 14})
            self.assertEqual(rets[1]["m"], {})
            self.assertEqual((lines[0]["ring"], lines[0]["window"]), ("pow2", 16))
            watch = [x for x in lines if x["k"] == "watch"]
            # every watched word, each named; 0x80002000 changes in poll 5, outside its window 6-9: no line
            self.assertEqual([(x["w"], x["poll"], x["fn"], x["new"]) for x in watch],
                             [("80002004", 5, "f", "80001000"), ("80001000", 5, "g", "ab000000")])
            # a call window keeps every call in its polls, beside the first-N and power-of-two ring
            for polls, kept in (("", [0, 1, 2, 4, 8]), ("5", list(range(9))), ("6-9", [0, 1, 2, 4, 8])):
                one = path / f"w{polls}"
                run = subprocess.run([str(exe)], env=dict(env, N64R_LOCKSTEP_DIR=str(one), N64R_LOCKSTEP_CALLS="1",
                                                          N64R_LOCKSTEP_CALLS_POLLS=polls),
                                     capture_output=True, text=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stderr)
                got = [json.loads(x) for x in (one / "lockstep.jsonl").read_text().splitlines()]
                self.assertEqual([x["o"] for x in got if x["k"] == "call" and x["caller"] == "f" and x["fn"] == "f"],
                                 kept)
            plain = subprocess.run([str(exe)], env={k: v for k, v in env.items() if k != "N64R_LOCKSTEP_DIR"},
                                   capture_output=True, text=True, timeout=30)
            self.assertEqual(plain.returncode, 3)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(shutil.which("g++"), "needs g++")
class LockstepPollTest(unittest.TestCase):
    def test_three_polls_record_index_vi_pads_and_changed_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "driver.cpp").write_text(POLL_DRIVER)
            exe = path / "driver"
            subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", "-Werror", str(HOST / "lockstep.cpp"),
                            str(path / "driver.cpp"), "-o", str(exe), "-lpthread"], check=True, capture_output=True,
                           text=True)
            out = path / "ls"
            env = dict(os.environ, N64R_LOCKSTEP_DIR=str(out), N64R_LOCKSTEP_RAM="0x4000")
            result = subprocess.run([str(exe)], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = [json.loads(x) for x in (out / "lockstep.jsonl").read_text().splitlines()]
            polls = [x for x in lines if x["k"] == "poll"]
            self.assertEqual([(x["poll"], x["vi"], x["ns"]) for x in polls],
                             [(1, 2, 16683333), (2, 4, 33366666), (3, 6, 50049999)])
            self.assertEqual([x["pad"] for x in polls], [["00000000", "80000000"], ["900050b0", "00000000"],
                                                         ["0010ff01", "00200000"]])
            self.assertEqual([p[0] for p in polls[0]["pages"]], [0, 1, 2, 3])
            # the hit in between saw page 1 change too; the poll stream still lists it against poll 1
            self.assertEqual([p[0] for p in polls[1]["pages"]], [1])
            self.assertEqual(polls[2]["pages"], [])
            # a call between poll lines 1 and 2 falls in poll 1 (the port's reach_poll), not retrace 7
            self.assertEqual([(x["fn"], x["frame"]) for x in lines if x["k"] == "call"], [("f", 1)])
            hit = next(x for x in lines if x["k"] == "hit")
            self.assertEqual(len(hit["pages"]), 4)
            self.assertEqual(int(polls[0]["pages"][2][1], 16), fnv(bytes.fromhex("11223344") + bytes(4092)))
            self.assertEqual(int(polls[1]["pages"][0][1], 16), fnv(b"\xab" + bytes(4095)))
            self.assertEqual(sorted(p.name for p in out.iterdir()), ["lockstep.jsonl"])  # hashes only, no dump

    def test_dump_replay_writes_only_the_named_pages_at_its_poll_and_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            # returns 4 when the movie runs out: exit 0 means the dump poll ended the process
            (path / "driver.cpp").write_text(POLL_DRIVER.replace("return 0;\n}", "return 4;\n}"))
            exe = path / "driver"
            subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", "-Werror", str(HOST / "lockstep.cpp"),
                            str(path / "driver.cpp"), "-o", str(exe), "-lpthread"], check=True, capture_output=True,
                           text=True)
            out = path / "ls"
            out.mkdir()
            (out / "lockstep.jsonl").write_text("recorded\n")  # the record the dump replays: left as it is
            env = dict(os.environ, N64R_LOCKSTEP_DIR=str(out), N64R_LOCKSTEP_RAM="0x4000",
                       N64R_LOCKSTEP_DUMP="2:1,2")
            result = subprocess.run([str(exe)], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((out / "lockstep.jsonl").read_text(), "recorded\n")
            self.assertEqual(sorted(p.name for p in (out / "dump").iterdir()), ["2"])
            self.assertEqual(sorted(p.name for p in (out / "dump" / "2").iterdir()), ["dump.bin"])
            data = (out / "dump" / "2" / "dump.bin").read_bytes()
            self.assertEqual(len(data), 2 * (16 + 4096))
            got = {}
            for at in range(0, len(data), 16 + 4096):
                pg, zero, h = struct.unpack_from("<IIQ", data, at)
                got[pg] = data[at + 16:at + 16 + 4096]
                self.assertEqual((zero, fnv(got[pg])), (0, h))
            self.assertEqual(got[1], b"\xab" + bytes(4095))  # poll 2's state: the byte stored before it
            self.assertEqual(got[2], bytes.fromhex("11223344") + bytes(4092))
            bad = subprocess.run([str(exe)], env=dict(env, N64R_LOCKSTEP_DUMP="2:9"), capture_output=True,
                                 text=True, timeout=30)
            self.assertEqual(bad.returncode, 3)  # a page outside RDRAM: the recorder stays off
            self.assertIn("lockstep dump off", bad.stderr)

    def test_mask_zeroes_its_words_in_hashes_and_dumps_and_meta_repeats_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "driver.cpp").write_text(POLL_DRIVER.replace("return 0;\n}", "return 4;\n}"))
            exe = path / "driver"
            subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", "-Werror", str(HOST / "lockstep.cpp"),
                            str(path / "driver.cpp"), "-o", str(exe), "-lpthread"], check=True, capture_output=True,
                           text=True)
            out = path / "ls"
            mask = "80001000-80001004,2000-2004"  # page 1's first word (KSEG0) and page 2's (physical)
            env = dict(os.environ, N64R_LOCKSTEP_DIR=str(out), N64R_LOCKSTEP_RAM="0x4000", N64R_LOCKSTEP_MASK=mask)
            result = subprocess.run([str(exe)], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 4, result.stderr)
            lines = [json.loads(x) for x in (out / "lockstep.jsonl").read_text().splitlines()]
            self.assertEqual(lines[0]["mask"], mask)
            polls = [x for x in lines if x["k"] == "poll"]
            zero = fnv(bytes(4096))
            self.assertEqual(int(polls[0]["pages"][2][1], 16), zero)  # 11223344 masked
            self.assertEqual(polls[1]["pages"], [])  # the byte stored at a masked word changes no hash
            dumped = subprocess.run([str(exe)], env=dict(env, N64R_LOCKSTEP_DUMP="2:1,2"), capture_output=True,
                                    text=True, timeout=30)
            self.assertEqual(dumped.returncode, 0, dumped.stderr)
            data = (out / "dump" / "2" / "dump.bin").read_bytes()
            for at in range(0, len(data), 16 + 4096):
                _, _, h = struct.unpack_from("<IIQ", data, at)
                self.assertEqual((data[at + 16:at + 16 + 4096], h), (bytes(4096), zero))
            for bad in ("80001001-80001004", "80001000", "2004-2000", "0-8000"):
                off = subprocess.run([str(exe)], env=dict(env, N64R_LOCKSTEP_MASK=bad), capture_output=True,
                                     text=True, timeout=30)
                self.assertEqual(off.returncode, 3, bad)
                self.assertIn("N64R_LOCKSTEP_MASK", off.stderr)


# Per-thread pairs and the per-poll totals: main calls f three times and a second thread twice, before poll 1; one
# more call on main before poll 2; nothing before poll 3.
PAIR_DRIVER = r"""
#include <cstdint>
#include <thread>
extern "C" uint64_t n64host_golden_frame() { return 7; }
// main.cpp's table
extern "C" uint32_t n64host_replaced_vram(size_t i) { return i < 2 ? 0x802bef68u + 0x104u * (uint32_t)i : 0u; }
extern "C" { volatile unsigned long long n64host_tas_poll = 0; }
extern "C" int n64host_lockstep_on;
extern "C" void n64host_lockstep_entry(const char *, const uint8_t *, uint32_t, uint32_t, uint32_t, uint32_t,
                                       uint64_t, uint64_t, uint32_t);
extern "C" void n64host_lockstep_return(const char *, const uint8_t *, uint32_t, uint32_t, uint64_t, uint32_t);
extern "C" void n64host_lockstep_poll(uint64_t, uint64_t, uint64_t, const uint8_t *, const uint32_t *, unsigned);
static uint8_t rdram[0x4000];
static const char f[] = "f";
static void call() {
    n64host_lockstep_entry(f, rdram, 0, 0, 0, 0, 0, 0, 0x80003f00);
    n64host_lockstep_return(f, rdram, 0, 0, 0, 0x80003f00);
}
int main() {
    if (!n64host_lockstep_on) return 3;
    const uint32_t pad[1] = {0};
    call(); call();
    std::thread([] { call(); call(); }).join();
    call();
    n64host_tas_poll = 1;
    n64host_lockstep_poll(1, 2, 0, rdram, pad, 1);
    call();
    n64host_tas_poll = 2;
    n64host_lockstep_poll(2, 4, 0, rdram, pad, 1);
    n64host_tas_poll = 3;
    n64host_lockstep_poll(3, 6, 0, rdram, pad, 1);
    return 0;
}
"""


@unittest.skipUnless(shutil.which("g++"), "needs g++")
class LockstepPairTotalsTest(unittest.TestCase):
    def test_ordinals_are_per_thread_and_each_poll_lists_moved_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "driver.cpp").write_text(PAIR_DRIVER)
            exe = path / "driver"
            subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", "-Werror", str(HOST / "lockstep.cpp"),
                            str(path / "driver.cpp"), "-o", str(exe), "-lpthread"], check=True, capture_output=True,
                           text=True)
            out = path / "ls"
            env = dict(os.environ, N64R_LOCKSTEP_DIR=str(out), N64R_LOCKSTEP_RAM="0x4000")
            result = subprocess.run([str(exe)], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = [json.loads(x) for x in (out / "lockstep.jsonl").read_text().splitlines()]
            self.assertEqual((lines[0]["ordinal"], lines[0]["totals"]), ("thread", "poll"))
            pairs = [x for x in lines if x["k"] == "pair"]
            self.assertEqual([(x["id"], x["caller"], x["fn"]) for x in pairs], [(0, "?", "f"), (1, "?", "f")])
            self.assertNotEqual(pairs[0]["thr"], pairs[1]["thr"])
            calls = [x for x in lines if x["k"] == "call"]
            # the function's call index i runs over both threads; the ordinal o is the thread's own
            self.assertEqual([(x["i"], x["o"], x["thr"] == pairs[0]["thr"]) for x in calls],
                             [(0, 0, True), (1, 1, True), (2, 0, False), (3, 1, False), (4, 2, True), (5, 3, True)])
            tots = [(x["poll"], x["d"]) for x in lines if x["k"] == "tot"]
            self.assertEqual(tots, [(1, [[0, 3], [1, 2]]), (2, [[0, 4]])])  # poll 3: nothing moved, no line
            order = [x["k"] for x in lines if x["k"] in ("tot", "poll")]
            self.assertEqual(order, ["tot", "poll", "tot", "poll", "poll"])  # a poll's totals precede its line
