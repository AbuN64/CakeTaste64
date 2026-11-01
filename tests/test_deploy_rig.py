"""The rig deploy steps: each `.deploy` step run through a fake bash seam; the suite, live-link and runner scripts run
for real in a tiny repository or a temp link."""

import hashlib
import importlib.util
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ci/deploy_rig.py"
spec = importlib.util.spec_from_file_location("deploy_rig", SCRIPT)
deploy_rig = importlib.util.module_from_spec(spec)
sys.modules["deploy_rig"] = deploy_rig
spec.loader.exec_module(deploy_rig)

SHA = "7a6ab5ce4712939ea310ee62e2fbc759920f6363"
NEWER = "c" * 40
HASH = "63576cbe05149fa7"
CHECKOUT = "/home/abu/runs/b.c/Projects/AbuN64/CakeTaste64"
ORACLE_LINK = deploy_rig.ORACLE_LINK
TOOLKIT = "/home/abu/venv-taste"


def suite_log(sha=SHA, imported=f"{CHECKOUT}/src/cake_taste/__init__.py", failed=None):
    lines = [f"suite-checkout: {CHECKOUT}", f"suite-sha: {sha}", f"suite-import: {imported}"]
    if failed:
        lines += ["suite-summary: 1766 passed, 1 failed, 5 skipped in 204 s", f"suite-first-failure: {failed}"]
    else:
        lines.append("suite-summary: 1767 passed, 0 failed, 5 skipped in 204 s")
    return "\n".join(lines)


def readback_log(live_sha, second=HASH):
    return (f"readback-live: {ORACLE_LINK}.{live_sha}\nreadback-func-names: 0\nreadback-os-pi-start-dma: named\n"
            f"readback-hash: {HASH}\nreadback-hash: {second}")


RUNTIME_DEFINES = ("osViGetCurrentFramebuffer", "osViBlack", "osSpTaskStartGo", "osSpTaskYield", "osVirtualToPhysical",
                   "osDpSetNextBuffer", "osPiStartDma")


def write_symbol_lists(build: Path) -> None:
    """N64Recomp's symbol_lists.cpp as deps.sh leaves it: the reimplemented block, then the ignored one."""
    (build / "deps/N64Recomp/src").mkdir(parents=True)
    names = "".join(f'    "{n}",\n' for n in RUNTIME_DEFINES)
    (build / "deps/N64Recomp/src/symbol_lists.cpp").write_text(
        f"const std::unordered_set<std::string> N64Recomp::reimplemented_funcs {{\n{names}}};\n"
        'const std::unordered_set<std::string> N64Recomp::ignored_funcs {\n    "__osViInit",\n};\n')


PINS_SCRIPT = deploy_rig.ORACLE.split("<<'PY'\n")[1].split("\nPY\n")[0]
PINCHECK_SCRIPT = deploy_rig.READBACK.split("<<'PY'\n")[1].split("\nPY\n")[0]
PIN = "a67227a02a5dd6b5e0aa8e67c1172d2372a60154"


def pins_json(tmp: Path, commit: str, addrs: bytes) -> dict:
    """The pins script run for real over a gen dir that keeps the given symbol_addrs.txt."""
    (tmp / "gen").mkdir(exist_ok=True)
    for name in ("cpu_profile.h", "cpu_native.h"):
        (tmp / "gen" / name).write_bytes(name.encode())
    (tmp / "gen/symbol_addrs.txt").write_bytes(addrs)
    (tmp / "facts.json").write_bytes(b"facts")
    (tmp / "old.json").write_text('{"wall": 2, "cpu": 2}')
    argv = ["python3", "-", "sha", *(str(tmp / n) for n in ("old.json", "gen", "facts.json")), commit]
    return json.loads(subprocess.run(argv, input=PINS_SCRIPT, capture_output=True, text=True, check=True).stdout)


class Shape(unittest.TestCase):
    def test_deploy_file_is_one_step_grammar_line_per_step_and_the_script_is_executable(self):
        lines = (ROOT / ".deploy").read_text().splitlines()
        self.assertEqual(lines[0], f"deploy-env TOOLKIT={TOOLKIT}")
        lines = lines[1:]
        steps = [line.split()[-1] for line in lines]
        self.assertEqual(steps, ["suite", "toolkit", "oracle", "readback", "commands"])
        self.assertEqual(steps, list(deploy_rig.STEPS))
        prefixes = ("rig run ci/deploy_rig.py ", "rig activate ci/deploy_rig.py /")
        self.assertTrue(all(line.startswith(prefixes) for line in lines))
        self.assertTrue((ROOT / "ci/deploy_rig.py").stat().st_mode & stat.S_IXUSR)
        self.assertTrue(SCRIPT.read_text().startswith("#!/usr/bin/env python3\n"))

    def test_scripts_parse_and_no_commit_pin_or_job_invoke_remains(self):
        scripts = (
            deploy_rig.ORACLE,
            deploy_rig.READBACK,
            deploy_rig.LIVE,
            deploy_rig.SUITE,
            deploy_rig.TOOLKIT_BUILD,
            deploy_rig.TOOLKIT_READBACK,
        )
        for body in scripts:
            self.assertEqual(subprocess.run(["bash", "-n"], input=body, text=True, check=False).returncode, 0)
        source = SCRIPT.read_text()
        for retired in ("finish/", "flock", "waitfor", "ssh", "-j8", "Job Invoke", "butlermaster"):
            self.assertNotIn(retired, source)

    def test_the_links_this_deploy_activates_are_its_own_toolkit_and_live_oracle(self):
        lines = (ROOT / ".deploy").read_text().splitlines()
        activated = [line.split()[3] for line in lines if line.startswith("rig activate ")]
        self.assertEqual(activated, [TOOLKIT, deploy_rig.ORACLE_LINK])
        self.assertEqual([line for line in lines if line.startswith("deploy-env")], [f"deploy-env TOOLKIT={TOOLKIT}"])
        source = SCRIPT.read_text()
        for toolkit_fact in ("venv-decomp", "venv-unbake", "decomp tools", "--carry-from"):
            self.assertNotIn(toolkit_fact, source.split('"""', 2)[2])
        self.assertFalse(hasattr(deploy_rig, "UNBAKE"))

    def test_this_deploy_publishes_ruff_and_no_command_another_toolkit_publishes(self):
        self.assertEqual(deploy_rig.PUBLISHED, ("ruff",))
        for taken in ("decomp", "unbake", "unbake-run"):
            self.assertNotIn(taken, deploy_rig.PUBLISHED)
        gate = (ROOT / "ci/check").read_text()
        self.assertIn('\n"$venv/bin/python" -m ruff check src tests\n', gate)
        self.assertIn("pip install -q -e '.[dev]'", gate)
        self.assertNotIn("\nruff ", gate)

    def test_the_build_resets_the_copied_deps_and_removes_a_failed_build_folder(self):
        for needle in ("reset -q --hard", 'trap \'rc=$?; test "$rc" = 0 || rm -rf "$BUILD"\' EXIT'):
            self.assertIn(needle, deploy_rig.ORACLE)
        self.assertLess(deploy_rig.ORACLE.index("cp -a"), deploy_rig.ORACLE.index("reset -q --hard"))
        self.assertLess(deploy_rig.ORACLE.index("reset -q --hard"), deploy_rig.ORACLE.index("deps.sh"))

    def test_the_recompile_runs_in_order_and_the_host_is_configured_from_its_gen_dir(self):
        build = deploy_rig.ORACLE
        steps = [
            "sh hosts/n64recomp/deps.sh",  # N64Recomp at its pin, patched
            "sh hosts/n64recomp/recompile.sh",  # N64Recomp built, decomp ELF, profile, generate, no unnamed
            "cmake -S hosts/n64recomp",  # n64host fed from the generated dir
            'cmake --build "$BUILD/build"',
            'selfcheck "$BUILD/n64host"',
        ]
        at = [build.index(step) for step in steps]
        self.assertEqual(at, sorted(at))
        self.assertIn('-DN64R_GEN_DIR="$gen"', build)
        self.assertIn(f"gen=$BUILD/recomp/{deploy_rig.VERSION}", build)
        self.assertNotIn("rig-state/behave/gen", build)
        self.assertIn(f'"$src" {deploy_rig.RAGEWARS} {deploy_rig.RAGEWARS_LOCAL} "$unbake" ', build)
        self.assertIn("unbake=${2:?", build)
        self.assertNotIn("__", build.replace("__ORACLE_LINK__", ""))
        self.assertLess(len(build.encode()), 4096)  # Job Invoke bounds one Argv item

    def test_the_oracle_calls_recompile_sh_with_one_command_of_exactly_its_seven_arguments(self):
        build = deploy_rig.ORACLE.replace("\\\n", " ")
        line = next(ln for ln in build.splitlines() if ln.startswith("sh hosts/n64recomp/recompile.sh"))
        self.assertEqual(len(shlex.split(line)), 1 + 1 + 7, line)
        self.assertEqual(shlex.split(line)[-4:], [deploy_rig.RAGEWARS_LOCAL, "$unbake",
                                                  deploy_rig.RECOMP_PROFILE, deploy_rig.VERSION])

    def test_recompile_sh_runs_its_steps_in_order_and_stops_on_an_unnamed_address(self):
        text = (ROOT / "hosts/n64recomp/recompile.sh").read_text()
        steps = ['cmake --build "$build/deps/N64Recomp/build"', 'sh "$src/hosts/n64recomp/decomp_elf.sh"',
                 'python3 "$src/hosts/n64recomp/recomp_profile.py"', "python3 -m cake_taste", 'test -z "$unnamed"',
                 'echo "func_names=0"']
        at = [text.index(step) for step in steps]
        self.assertEqual(at, sorted(at))
        self.assertIn('echo "$unnamed" >&2; exit 1', text)
        self.assertIn("(func|asm|entry)_[0-9A-Fa-f]{8}", text)
        self.assertEqual(subprocess.run(["sh", "-n", str(ROOT / "hosts/n64recomp/recompile.sh")]).returncode, 0)

    def test_the_derived_profile_points_every_recomp_path_into_the_build_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live.json"
            old = {"project_root": "/old", "runtime_root": "/old", "output_root": "/old"}
            recomp = {"decomp_root": "/old", "gen_root": "/old/gen", "elf": "e",
                      "symbols_file": "versions/{version}/symbol_addrs.txt",
                      "tools": {"n64recomp": "/old/a", "rsprecomp": "/old/b"},
                      "symbol_names": {"us-rev1": {"0x802BC3B0": "osAiSetFrequency", "0x802BEB30": "osPiRawStartDma",
                                                   "0x802BF440": {"name": "osCreateViManager", "source": "x"}}}}
            live.write_text(json.dumps({"name": "behave", **old, "recomp": recomp}))
            host_names = ["osViGetCurrentFramebuffer", "osViBlack", "osSpTaskStartGo", "osVirtualToPhysical",
                          "osDpSetNextBuffer"]
            build = Path(tmp) / "b"
            write_symbol_lists(build)
            (build / "decomp/versions/us-rev1").mkdir(parents=True)
            (build / "decomp/versions/us-rev1/symbol_addrs.txt").write_text(
                "osAiSetFrequency = 0x802BC3B0; // type:func\n__osPiRawStartDma = 0x802BEB30; // type:func\n"
                "System_CreateVideoManager = 0x802BF440; // type:func\n"
                "osPiStartDma = 0x80310000; // type:func\n__osViInit = 0x80311000; // type:func\n"
                + "".join(f"{n} = 0x{0x80300000 + 16 * i:08X}; // type:func\n" for i, n in enumerate(host_names)))
            script = ROOT / "hosts/n64recomp/recomp_profile.py"
            done = subprocess.run(["python3", str(script), str(live), str(build)], capture_output=True, text=True,
                                  check=True)
            derived = json.loads(done.stdout)
            b = str(build)
        self.assertEqual(derived["project_root"], b + "/decomp")
        self.assertEqual(derived["recomp"]["decomp_root"], b + "/decomp")
        self.assertEqual(derived["recomp"]["gen_root"], b + "/recomp")
        self.assertEqual(derived["recomp"]["tools"], {"n64recomp": b + "/deps/N64Recomp/build/N64Recomp",
                                                      "rsprecomp": b + "/deps/N64Recomp/build/RSPRecomp"})
        # the host binds the decomp's names for the two dropped entries, and the patch keeps libultra originals as _kept
        link = (ROOT / "hosts/n64recomp/CMakeLists.txt").read_text()
        for bound in ("--wrap=__osPiRawStartDma_kept\n", "--wrap=System_CreateVideoManager\n"):
            self.assertIn(bound, link)
        for old in ("osPiRawStartDma_recomp", "osCreateViManager_recomp"):
            self.assertNotIn(old, link)
        diff = ROOT / "hosts/n64recomp/patches/n64recomp-runtime-funcs.diff"
        self.assertIn('func.name + "_kept"', diff.read_text())
        # an entry the decomp names differently is dropped and listed: that function stays recompiled as named
        names = derived["recomp"]["symbol_names"]["us-rev1"]
        self.assertEqual(names["0x802BC3B0"], "osAiSetFrequency")
        self.assertNotIn("0x802BEB30", names)
        # every reimplemented function of a HOST_RUNTIME family (VI, SP, DP, TLB) runs the runtime's version, at the
        # decomp's address; the PI function and the ignored __osViInit stay kept
        self.assertEqual(sorted(e["name"] for a, e in names.items() if a != "0x802BC3B0"), sorted(host_names))
        self.assertEqual(names["0x80300000"]["name"], host_names[0])
        self.assertIn("HOST_RUNTIME", names["0x80300000"]["source"])
        self.assertIn("0x802BEB30 osPiRawStartDma (the decomp names it __osPiRawStartDma)", done.stderr)
        self.assertIn("0x802BF440", done.stderr)

    def test_a_host_runtime_family_the_decomp_does_not_name_stops_the_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live.json"
            recomp = {"symbols_file": "versions/{version}/symbol_addrs.txt", "tools": {},
                      "symbol_names": {"us-rev1": {}}}
            live.write_text(json.dumps({"name": "behave", "recomp": recomp}))
            build = Path(tmp) / "b"
            write_symbol_lists(build)
            (build / "decomp/versions/us-rev1").mkdir(parents=True)
            (build / "decomp/versions/us-rev1/symbol_addrs.txt").write_text("osViBlack = 0x80300000; // type:func\n")
            done = subprocess.run(["python3", str(ROOT / "hosts/n64recomp/recomp_profile.py"), str(live), str(build)],
                                  capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("HOST_RUNTIME family (?:__)?osSp", done.stderr)

    def test_deps_sh_prepares_n64recomp_with_its_patches_before_the_runtime(self):
        text = (ROOT / "hosts/n64recomp/deps.sh").read_text()
        recomp = text.index("prepare N64Recomp https://github.com/N64Recomp/N64Recomp")
        self.assertLess(recomp, text.index("prepare N64ModernRuntime"))
        patches = text[recomp:text.index("prepare N64ModernRuntime")]
        jtbl, funcs = "n64recomp-jtbl-kuseg-alias-and-bounds.diff", "n64recomp-runtime-funcs.diff"
        self.assertLess(patches.index(jtbl), patches.index(funcs))
        for name in (jtbl, funcs):
            self.assertTrue((ROOT / "hosts/n64recomp/patches" / name).is_file())

    def test_the_build_uses_the_job_width_and_the_activation_folder(self):
        self.assertIn('cmake --build "$BUILD/build" -j"$JOBS"', deploy_rig.ORACLE)
        self.assertIn('mkdir "$BUILD"', deploy_rig.ORACLE)
        self.assertTrue(deploy_rig.ORACLE.rstrip().endswith('selfcheck "$BUILD/n64host"'))
        self.assertIn(f'selfcheck "{ORACLE_LINK}/n64host"', deploy_rig.READBACK)

    def test_the_charge_headers_ship_in_the_snapshot_and_are_hashed_in_its_pins(self):
        build = deploy_rig.ORACLE
        copy = (
            'install -m 644 "$BUILD/build/gen/cpu_profile.h" "$BUILD/build/gen/cpu_native.h" '
            '"$gen/symbols.toml" "$BUILD/gen/"'
        )
        self.assertIn(copy, build)
        self.assertLess(build.index(copy), build.index('rm -rf "$BUILD/deps" "$BUILD/build"'))
        self.assertLess(build.index(copy), build.index("oracle-pins.json"))
        self.assertIn('"gen": gen', build)

    def test_the_pins_script_hashes_both_headers_for_real(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "gen").mkdir()
            (tmp / "gen/cpu_profile.h").write_bytes(b"profile")
            (tmp / "gen/cpu_native.h").write_bytes(b"native")
            (tmp / "gen/symbol_addrs.txt").write_bytes(b"osPiStartDma = 0x802BDDC0;")
            (tmp / "facts.json").write_bytes(b"facts")
            (tmp / "old.json").write_text('{"wall": 2, "cpu": 2}')
            argv = ["python3", "-", "sha", *(str(tmp / n) for n in ("old.json", "gen", "facts.json")), "d" * 40]
            out = subprocess.run(argv, input=PINS_SCRIPT, capture_output=True, text=True, check=True).stdout
            pins = json.loads(out)
            gen = pins["gen"]
            self.assertEqual(pins["facts"], hashlib.sha256(b"facts").hexdigest())
            self.assertEqual(gen["cpu_profile.h"], hashlib.sha256(b"profile").hexdigest())
            self.assertEqual(gen["cpu_native.h"], hashlib.sha256(b"native").hexdigest())



class DecompPins(unittest.TestCase):
    def test_a_changed_pin_changes_oracle_pins_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            base = pins_json(tmp, PIN, b"osPiStartDma = 0x802BDDC0;")
            self.assertEqual(base["decomp"], {"commit": PIN, "symbol_addrs": hashlib.sha256(
                b"osPiStartDma = 0x802BDDC0;").hexdigest()})
            self.assertEqual(pins_json(tmp, PIN, b"osPiStartDma = 0x802BDDC0;")["decomp"], base["decomp"])
            self.assertNotEqual(pins_json(tmp, "e" * 40, b"osPiStartDma = 0x802BDDC0;")["decomp"], base["decomp"])
            self.assertNotEqual(pins_json(tmp, PIN, b"osPiStartDma = 0x802BDDC4;")["decomp"], base["decomp"])

    def test_the_oracle_keeps_the_pinned_symbol_addrs_beside_the_headers(self):
        build = deploy_rig.ORACLE
        keep = 'install -m 644 "$BUILD/decomp/versions/us-rev1/symbol_addrs.txt" "$BUILD/gen/"'
        self.assertIn(keep, build)
        self.assertLess(build.index(keep), build.index('rm -rf "$BUILD/deps" "$BUILD/build" "$BUILD/decomp"'))
        self.assertIn('"$(git -C "$BUILD/decomp" rev-parse HEAD)"', build)

    def readback(self, tmp: Path, native=b'{"osPiStartDma", 0},', pin=PIN, commit=PIN, addrs=b"a"):
        pins = pins_json(tmp, commit, addrs)
        (tmp / "gen/cpu_native.h").write_bytes(native)
        pins["gen"]["cpu_native.h"] = hashlib.sha256(native).hexdigest()
        (tmp / "oracle-pins.json").write_text(json.dumps(pins))
        (tmp / "PINS").write_text(f"N64Recomp ffb\nRageWars {pin}\n")
        argv = ["python3", "-", *(str(tmp / n) for n in ("oracle-pins.json", "PINS", "gen"))]
        return subprocess.run(argv, input=PINCHECK_SCRIPT, capture_output=True, text=True)

    def test_the_readback_checks_the_pins_and_the_deployed_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ok = self.readback(tmp)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn(f"readback-decomp: {PIN}", ok.stdout)
            self.assertIn("readback-func-names: 0", ok.stdout)
            self.assertIn("readback-os-pi-start-dma: named", ok.stdout)
            for message, bad in (
                ("recompiled from", self.readback(tmp, commit="f" * 40)),
                ("1 unnamed functions: func_80204868_de",
                 self.readback(tmp, native=b'{"osPiStartDma", 0},{"func_80204868_de", 1},')),
                ("does not name osPiStartDma", self.readback(tmp, native=b'{"osSendMesg", 0},')),
            ):
                self.assertEqual(bad.returncode, 1, message)
                self.assertIn(message, bad.stderr)
            # a deployed file edited after the pins were written no longer hashes to its pin
            argv = ["python3", "-", *(str(tmp / n) for n in ("oracle-pins.json", "PINS", "gen"))]
            self.readback(tmp)
            for name, content, message in (("symbol_addrs.txt", b"edited", "kept symbol_addrs.txt is not the pinned"),
                                           ("cpu_native.h", b'"osPiStartDma" ', "cpu_native.h is not the pinned")):
                self.readback(tmp)
                (tmp / "gen" / name).write_bytes(content)
                bad = subprocess.run(argv, input=PINCHECK_SCRIPT, capture_output=True, text=True)
                self.assertEqual(bad.returncode, 1)
                self.assertIn(message, bad.stderr)

    def test_the_readback_runs_the_pins_check_before_the_self_check_in_the_tree(self):
        self.assertIn("hosts/n64recomp/PINS", deploy_rig.READBACK)
        self.assertLess(deploy_rig.READBACK.index("oracle-pins.json"), deploy_rig.READBACK.rindex("selfcheck "))


class DecompElfScript(unittest.TestCase):
    """decomp_elf.sh run for real against a throwaway repository, with a fake unbake toolkit and `make` on PATH."""

    def run_script(self, tmp):
        tmp = Path(tmp)
        src = tmp / "src"
        subprocess.run(["git", "init", "-q", str(src)], check=True)
        (src / "Makefile").write_text("x\n")
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.lan", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@example.lan", "PATH": os.environ["PATH"], "HOME": str(tmp)}
        subprocess.run(["git", "-C", str(src), "add", "."], check=True, env=env)
        subprocess.run(["git", "-C", str(src), "commit", "-qm", "c"], check=True, env=env)
        pin = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], check=True, capture_output=True,
                             text=True).stdout.strip()
        hosts = tmp / "hosts"
        hosts.mkdir()
        (hosts / "PINS").write_text(f"RageWars {pin}\n")
        script = hosts / "decomp_elf.sh"
        script.write_text((ROOT / "hosts/n64recomp/decomp_elf.sh").read_text())
        local = tmp / "local"
        (local / "roms").mkdir(parents=True)
        (local / "roms/us.z64").write_text("rom")
        (local / "local.mk").write_text("OBJSTORE := stale-toolkit\n")
        bin_dir = tmp / "toolkit/bin"
        bin_dir.mkdir(parents=True)
        log = tmp / "calls"
        fakes = {
            "unbake": 'echo "toolkit-unbake $* in $(basename "$PWD")" >> "$CALLS"; '
                      'echo "OBJSTORE := current-toolkit" > local.mk\n',
            "make": 'echo "make $* local.mk=$(cat "$2/local.mk")" >> "$CALLS"; mkdir -p "$2/build/v1"; '
                    'echo elf > "$2/build/v1/RageWars.elf"\n',
        }
        for name, body in fakes.items():
            (bin_dir / name).write_text("#!/bin/sh\n" + body)
            (bin_dir / name).chmod(0o755)
        bin_make = tmp / "bin"
        bin_make.mkdir()
        (bin_dir / "make").rename(bin_make / "make")
        env |= {"PATH": f"{bin_make}:{os.environ['PATH']}", "RAGEWARS_LOCAL": str(local), "JOBS": "3",
                "CALLS": str(log)}
        argv = ["sh", str(script), str(src), str(tmp / "dest"), "v1", str(tmp / "toolkit")]
        done = subprocess.run(argv, env=env, capture_output=True, text=True)
        return done, tmp / "dest", log, pin

    def test_local_mk_is_written_in_the_clone_by_unbake_and_only_roms_are_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            done, dest, log, pin = self.run_script(tmp)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual((dest / "local.mk").read_text(), "OBJSTORE := current-toolkit\n")
            self.assertEqual((dest / "roms/us.z64").read_text(), "rom")
            calls = log.read_text().splitlines()
            self.assertEqual(calls[0], "toolkit-unbake setup --no-make in dest")
            self.assertTrue(calls[1].startswith("make -C "), calls[1])
            self.assertIn("-j3 build/v1/RageWars.elf local.mk=OBJSTORE := current-toolkit", calls[1])
            self.assertIn(f"decomp_elf: {pin} v1", done.stdout)

    def test_a_failed_setup_stops_before_make(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "x").write_text("")
            text = (ROOT / "hosts/n64recomp/decomp_elf.sh").read_text()
            self.assertLess(text.index("unbake setup --no-make"), text.index("make -C"))
            self.assertNotIn('cp "$local_dir/local.mk"', text)
            self.assertIn("set -eu", text)


class ToolkitBuildScript(unittest.TestCase):
    """TOOLKIT_BUILD run for real with a fake `python3` that makes a venv whose python installs or fails on demand."""

    def build(self, tmp, fail=None, live=False):
        shim = tmp / "shim"
        shim.mkdir(exist_ok=True)
        (shim / "python3").write_text(
            """#!/bin/sh
mkdir -p "$3/bin" && cat > "$3/bin/python" <<'EOF'
#!/bin/sh
case "$1 $2" in "-m pip") exit "${PIP_EXIT:-0}" ;; esac
cat > /dev/null
exit "${PIN_EXIT:-0}"
EOF
chmod +x "$3/bin/python"
"""
        )
        (shim / "python3").chmod(0o755)
        toolkit, build = tmp / "venv-taste", tmp / f"venv-taste.{SHA}"
        if live:
            (build / "bin").mkdir(parents=True)
            (build / "bin/python").write_text("")
            (build / "bin/python").chmod(0o755)
            toolkit.symlink_to(build)
        env = {**os.environ, "PATH": f"{shim}:{os.environ['PATH']}", "BUILD": str(build), **(fail or {})}
        done = subprocess.run(
            ["bash", "-c", deploy_rig.TOOLKIT_BUILD, "step", str(toolkit), "ruff==0.16.9"],
            env=env, capture_output=True, text=True, check=False,
        )
        return done, build

    def test_a_build_makes_the_venv_at_build_and_a_failed_install_or_pin_removes_it(self):
        with tempfile.TemporaryDirectory() as raw:
            done, build = self.build(Path(raw))
            made = f"toolkit-build: {build}" in done.stdout
            self.assertEqual((done.returncode, made, build.is_dir()), (0, True, True))
        for failure in ({"PIP_EXIT": "1"}, {"PIN_EXIT": "1"}):
            with tempfile.TemporaryDirectory() as raw:
                done, build = self.build(Path(raw), fail=failure)
                self.assertEqual((done.returncode, build.exists()), (1, False), done.stderr)

    def test_an_existing_build_that_is_not_live_refuses_and_a_live_build_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            (tmp / f"venv-taste.{SHA}").mkdir()
            done, build = self.build(tmp)
            self.assertEqual((done.returncode, "exists" in done.stderr, build.is_dir()), (1, True, True))
        with tempfile.TemporaryDirectory() as raw:
            done, _ = self.build(Path(raw), live=True)
            self.assertEqual((done.returncode, "live already" in done.stdout), (0, True))


class FakeRun:
    """The bash seam: answers each script with the next scripted (exit code, stdout) and records the calls."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, script, *args, capture=False):
        self.calls.append((script, args, capture))
        return self.answers.pop(0)


ENVIRON = {deploy_rig.ENV: SHA, "TOOLKIT": TOOLKIT}


def step(argv, run, environ=ENVIRON):
    with redirect_stdout(StringIO()) as out, redirect_stderr(StringIO()) as err:
        code = deploy_rig.main(argv, environ, run)
    return code, out.getvalue(), err.getvalue()


class Host:
    """A throwaway rig home: the toolkit link, the command directory beside it and CakeUnbake64's published unbake."""

    def __init__(self, unbake=True):
        self.published = unbake

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        self.toolkit = str(home / "venv-taste")
        self.commands = home / "commands"
        self.commands.mkdir()
        self.unbake = home / "venv-unbake.abc"
        (self.unbake / "bin").mkdir(parents=True)
        (self.unbake / "bin/unbake").write_text("#!/bin/sh\n")
        if self.published:
            (self.commands / "unbake").symlink_to(home / "venv-unbake.abc/bin/unbake")
        live = home / f"venv-taste.{SHA}"
        (live / "bin").mkdir(parents=True)
        (live / "bin/ruff").write_text("#!/bin/sh\n")
        Path(self.toolkit).symlink_to(live)
        self.environ = {deploy_rig.ENV: SHA, "TOOLKIT": self.toolkit}
        return self

    def __exit__(self, *exc):
        self.tmp.cleanup()


class Steps(unittest.TestCase):
    def setUp(self):  # the steps read pyproject.toml from the tree they run in
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(ROOT)

    def test_an_unknown_step_and_a_bad_sha_refuse_before_any_script(self):
        run = FakeRun()
        for argv, environ in (
            (["nothing"], ENVIRON),
            ([], ENVIRON),
            (["oracle"], {**ENVIRON, deploy_rig.ENV: "abc"}),
            (["oracle"], {}),
        ):
            code, _, err = step(argv, run, environ)
            self.assertEqual((code, err.startswith("deploy refused: ")), (1, True))
        self.assertEqual(run.calls, [])

    def test_oracle_builds_at_the_sha_with_the_unbake_toolkit_found_by_command_and_a_failed_build_refuses(self):
        with Host() as host:
            run = FakeRun((0, ""))
            self.assertEqual(step(["oracle"], run, host.environ)[0], 0)
            self.assertEqual(run.calls[0][:2], (deploy_rig.ORACLE, (SHA, str(host.unbake.resolve()))))
            code, _, err = step(["oracle"], FakeRun((1, "")), host.environ)
            self.assertEqual((code, "oracle build exit 1, links unchanged" in err), (1, True))

    def test_oracle_refuses_before_any_script_when_unbake_is_not_published(self):
        with Host(unbake=False) as host:
            run = FakeRun()
            code, _, err = step(["oracle"], run, host.environ)
            self.assertEqual((code, "is not published" in err), (1, True))
            self.assertEqual(run.calls, [])

    def test_toolkit_installs_the_pinned_dev_extra_of_the_tree_and_a_failed_build_refuses(self):
        run = FakeRun((0, ""))
        self.assertEqual(step(["toolkit"], run)[0], 0)
        self.assertEqual(run.calls[0][:2], (deploy_rig.TOOLKIT_BUILD, (TOOLKIT, "ruff==0.16.9")))
        code, _, err = step(["toolkit"], FakeRun((1, "")))
        self.assertEqual((code, "toolkit build exit 1, links unchanged" in err), (1, True))

    def test_commands_reads_the_toolkit_back_then_publishes_ruff_by_name(self):
        with Host() as host:
            run = FakeRun((0, "toolkit-pin: ruff 0.16.9\n"))
            code, out, _ = step(["commands"], run, host.environ)
            self.assertEqual(code, 0, out)
            args = (host.toolkit, f"{host.toolkit}.{SHA}", "ruff==0.16.9")
            self.assertEqual(run.calls[0][:2], (deploy_rig.TOOLKIT_READBACK, args))
            self.assertIn(f"publish: ruff -> {host.commands}", out)
            self.assertEqual(os.readlink(host.commands / "ruff"), f"{host.toolkit}/bin/ruff")
            code, _, err = step(["commands"], FakeRun((1, "toolkit: ruff is 0.1")), host.environ)
            self.assertEqual((code, "toolkit readback exit 1: toolkit: ruff is 0.1" in err), (1, True))

    def test_a_toolkit_env_that_is_not_an_absolute_path_refuses_before_any_script(self):
        run = FakeRun()
        for environ in ({deploy_rig.ENV: SHA}, {deploy_rig.ENV: SHA, "TOOLKIT": "venv-taste"}):
            code, _, err = step(["toolkit"], run, environ)
            self.assertEqual((code, "TOOLKIT is not an absolute path" in err), (1, True))
        self.assertEqual(run.calls, [])

    def test_readback_needs_two_equal_hashes_and_the_live_link_at_the_sha(self):
        self.assertEqual(step(["readback"], FakeRun((0, readback_log(SHA))))[0], 0)
        for log, code in (
            (readback_log(SHA, second="f" * 16), 0),
            (readback_log(NEWER), 0),
            (readback_log(SHA), 1),
            (f"readback-live: {ORACLE_LINK}.{SHA}", 0),
            (readback_log(SHA).replace("func-names: 0", "func-names: 3"), 0),
            (readback_log(SHA).replace("os-pi-start-dma: named\n", ""), 0),
        ):
            self.assertEqual(step(["readback"], FakeRun((code, log)))[0], 1, log)

    def test_the_suite_step_accepts_a_clean_suite_and_names_every_refusal(self):
        self.assertEqual(step(["suite"], FakeRun((0, suite_log())))[0], 0)
        cases = (
            ((1, suite_log(failed="test_x (t.T)")), "test_x (t.T)"),
            ((0, suite_log(imported="/elsewhere/cake_taste/__init__.py")), "outside its checkout"),
            ((0, suite_log(imported=f"{CHECKOUT}-sibling/src/cake_taste/__init__.py")), "outside its checkout"),
            ((0, suite_log(sha=NEWER)), "not the landed"),
            ((1, "boom"), "did not start"),
            ((1, suite_log().replace("suite-summary", "x")), "no summary"),
        )
        for answer, needle in cases:
            code, _, err = step(["suite"], FakeRun(answer))
            self.assertEqual((code, needle in err), (1, True), (answer, err))


class LiveLinkScript(unittest.TestCase):
    """The LIVE script run for real against a temp toolkit link: what the rig reads when the link is bad."""

    def run_live(self, prepare):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp, "oracle")
            prepare(link, Path(tmp))
            body = deploy_rig.LIVE.replace(ORACLE_LINK, str(link))
            done = subprocess.run(["bash", "-c", body], capture_output=True, text=True, check=False)
            return done.returncode, done.stdout.strip(), done.stderr.strip(), Path(tmp).resolve()

    def test_a_link_to_a_built_oracle_answers_its_resolved_target(self):
        def prepare(link, tmp):
            build = tmp / "oracle.abc"
            build.mkdir()
            (build / "n64host").write_text("#!/bin/sh\n")
            (build / "n64host").chmod(0o755)
            link.symlink_to(build)

        code, out, _, tmp = self.run_live(prepare)
        self.assertEqual((code, out), (0, f"readback-live: {tmp}/oracle.abc"))

    def test_a_dangling_link_exits_1_naming_the_link_and_the_cause(self):
        code, out, err, _ = self.run_live(lambda link, tmp: link.symlink_to(tmp / "gone"))
        self.assertEqual((code, out), (1, ""))
        self.assertRegex(err, r"live oracle link .*oracle is broken: .*gone has no n64host")

    def test_a_missing_link_or_a_plain_directory_is_not_a_symlink(self):
        code, _, err, _ = self.run_live(lambda link, tmp: None)
        self.assertEqual(code, 1)
        self.assertIn("is not a symlink", err)


def git(*args, cwd):
    identity = ["-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", *identity, *args], cwd=cwd, check=True, capture_output=True)


class SuiteScript(unittest.TestCase):
    """The SUITE script and RUNNER run for real in the Worktree, here a tiny local repository."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        for path in (self.repo / "src/cake_taste", self.repo / "tests"):
            path.mkdir(parents=True)
        (self.repo / "src/cake_taste/__init__.py").write_text("VALUE = 1\n")
        self.write_test("self.assertEqual(cake_taste.VALUE, 1)")
        git("init", "-q", "-b", "main", cwd=self.repo)
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "-m", "one", cwd=self.repo)

    def write_test(self, body):
        (self.repo / "tests/test_it.py").write_text(
            "import unittest\nimport cake_taste\n\n\nclass Case(unittest.TestCase):\n"
            f"    def test_it(self):\n        {body}\n"
        )

    def run_suite(self):
        script = deploy_rig.SUITE.replace("__RUNNER__", deploy_rig.RUNNER)
        done = subprocess.run(
            ["bash", "-c", script, "suite", sys.executable],
            cwd=self.repo,
            env={"PATH": os.environ["PATH"], "HOME": self.tmp.name},
            capture_output=True,
            text=True,
            check=False,
        )
        return done, deploy_rig.facts(done.stdout, "suite-")

    def test_a_passing_suite_reports_the_import_from_the_worktree_and_leaves_it_clean(self):
        done, facts = self.run_suite()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(facts["suite-checkout"], str(self.repo.resolve()))
        self.assertTrue(facts["suite-summary"].startswith("1 passed, 0 failed, 0 skipped in "))
        self.assertTrue(PurePosixPath(facts["suite-import"]).is_relative_to(facts["suite-checkout"]))
        status = subprocess.run(["git", "status", "--short"], cwd=self.repo, capture_output=True, text=True, check=True)
        self.assertEqual(status.stdout, "")

    def test_a_failing_test_exits_1_and_names_the_first_failure(self):
        self.write_test("self.assertEqual(cake_taste.VALUE, 2)")
        done, facts = self.run_suite()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(facts["suite-first-failure"], "test_it.Case.test_it")

    def test_a_test_printing_without_a_newline_does_not_swallow_the_facts(self):
        self.write_test("import sys; sys.stdout.write('kept-by-default')")
        done, facts = self.run_suite()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(facts["suite-summary"].startswith("1 passed, 0 failed, 0 skipped in "))

