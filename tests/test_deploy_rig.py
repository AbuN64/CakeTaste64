"""The rig deploy steps: each `.deploy` step run through a fake bash seam; the suite, live-link and runner scripts run
for real in a tiny repository or a temp link."""

import hashlib
import importlib.util
import json
import os
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
TOOLKIT = deploy_rig.TOOLKIT


def suite_log(sha=SHA, imported=f"{CHECKOUT}/src/cake_taste/__init__.py", failed=None):
    lines = [f"suite-checkout: {CHECKOUT}", f"suite-sha: {sha}", f"suite-import: {imported}"]
    if failed:
        lines += ["suite-summary: 1766 passed, 1 failed, 5 skipped in 204 s", f"suite-first-failure: {failed}"]
    else:
        lines.append("suite-summary: 1767 passed, 0 failed, 5 skipped in 204 s")
    return "\n".join(lines)


def readback_log(live_sha, second=HASH):
    return f"readback-live: {TOOLKIT}.{live_sha}\nreadback-hash: {HASH}\nreadback-hash: {second}"


class Shape(unittest.TestCase):
    def test_deploy_file_is_one_step_grammar_line_per_step_and_the_script_is_executable(self):
        lines = (ROOT / ".deploy").read_text().splitlines()
        self.assertEqual(lines[0], "deploy-env TOOLKIT=/home/abu/venv-decomp")
        steps = [line.split()[-1] for line in lines[1:]]
        self.assertEqual(steps, ["suite", "name", "install", "installed", "oracle", "readback"])
        self.assertEqual(steps, list(deploy_rig.STEPS))
        prefixes = ("rig run ci/deploy_rig.py ", "rig activate ci/deploy_rig.py /")
        self.assertTrue(all(line.startswith(prefixes) for line in lines[1:]))
        self.assertTrue((ROOT / "ci/deploy_rig.py").stat().st_mode & stat.S_IXUSR)
        self.assertTrue(SCRIPT.read_text().startswith("#!/usr/bin/env python3\n"))

    def test_scripts_parse_and_no_commit_pin_or_job_invoke_remains(self):
        scripts = (deploy_rig.ORACLE, deploy_rig.READBACK, deploy_rig.LIVE, deploy_rig.SUITE)
        for body in (*scripts, deploy_rig.NAME, deploy_rig.INSTALL, deploy_rig.INSTALLED):
            self.assertEqual(subprocess.run(["bash", "-n"], input=body, text=True, check=False).returncode, 0)
        source = SCRIPT.read_text()
        for retired in ("finish/", "flock", "waitfor", "ssh", "-j8", "Job Invoke", "butlermaster"):
            self.assertNotIn(retired, source)

    def test_the_install_is_the_one_package_of_this_repository_at_the_landed_sha(self):
        self.assertIn('tools deploy --package cake-taste-64 --sha "$sha" --venv "$stage"', deploy_rig.INSTALL)
        self.assertIn('tools name --package cake-taste-64 --sha "$sha" --carry-from "$toolkit"', deploy_rig.NAME)

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
        self.assertIn(f'"$src" {deploy_rig.RAGEWARS} {deploy_rig.RAGEWARS_LOCAL} ', build)
        self.assertNotIn("__", build.replace("__TOOLKIT__", ""))
        self.assertLess(len(build.encode()), 4096)  # Job Invoke bounds one Argv item

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
            host_names = [line.split()[0] for line in (ROOT / "hosts/n64recomp/HOST_RUNTIME").read_text().splitlines()
                          if line.strip() and not line.startswith("#")]
            build = Path(tmp) / "b"
            (build / "decomp/versions/us-rev1").mkdir(parents=True)
            (build / "decomp/versions/us-rev1/symbol_addrs.txt").write_text(
                "osAiSetFrequency = 0x802BC3B0; // type:func\n__osPiRawStartDma = 0x802BEB30; // type:func\n"
                "System_CreateVideoManager = 0x802BF440; // type:func\n"
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
        # the functions the host's VI and SP model owns run the runtime's version, at the decomp's address for each
        self.assertEqual(sorted(e["name"] for a, e in names.items() if a != "0x802BC3B0"), sorted(host_names))
        self.assertIn("osViGetCurrentFramebuffer", host_names)
        self.assertIn("osSpTaskStartGo", host_names)
        self.assertEqual(names["0x80300000"]["name"], host_names[0])
        self.assertIn("HOST_RUNTIME", names["0x80300000"]["source"])
        self.assertIn("0x802BEB30 osPiRawStartDma (the decomp names it __osPiRawStartDma)", done.stderr)
        self.assertIn("0x802BF440", done.stderr)

    def test_a_host_runtime_name_the_decomp_lacks_stops_the_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live.json"
            recomp = {"symbols_file": "versions/{version}/symbol_addrs.txt", "tools": {},
                      "symbol_names": {"us-rev1": {}}}
            live.write_text(json.dumps({"name": "behave", "recomp": recomp}))
            build = Path(tmp) / "b"
            (build / "decomp/versions/us-rev1").mkdir(parents=True)
            (build / "decomp/versions/us-rev1/symbol_addrs.txt").write_text("osViBlack = 0x80300000; // type:func\n")
            done = subprocess.run(["python3", str(ROOT / "hosts/n64recomp/recomp_profile.py"), str(live), str(build)],
                                  capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("osViGetCurrentFramebuffer", done.stderr)

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
        self.assertIn(f'selfcheck "{TOOLKIT}/n64host"', deploy_rig.READBACK)

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
            (tmp / "facts.json").write_bytes(b"facts")
            (tmp / "old.json").write_text('{"wall": 2, "cpu": 2}')
            script = deploy_rig.ORACLE.split("<<'PY'\n")[1].split("\nPY\n")[0]
            argv = ["python3", "-", "sha", *(str(tmp / n) for n in ("old.json", "gen", "facts.json"))]
            out = subprocess.run(argv, input=script, capture_output=True, text=True, check=True).stdout
            pins = json.loads(out)
            gen = pins["gen"]
            self.assertEqual(pins["facts"], hashlib.sha256(b"facts").hexdigest())
            self.assertEqual(gen["cpu_profile.h"], hashlib.sha256(b"profile").hexdigest())
            self.assertEqual(gen["cpu_native.h"], hashlib.sha256(b"native").hexdigest())



class FakeRun:
    """The bash seam: answers each script with the next scripted (exit code, stdout) and records the calls."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, script, *args, capture=False):
        self.calls.append((script, args, capture))
        return self.answers.pop(0)


ENVIRON = {deploy_rig.ENV: SHA, "TOOLKIT": "/home/abu/venv-decomp"}
NAME12 = "0123456789ab"
ENVELOPE = json.dumps({"result": {"name": NAME12}})


def step(argv, run, environ=ENVIRON):
    with redirect_stdout(StringIO()) as out, redirect_stderr(StringIO()) as err:
        code = deploy_rig.main(argv, environ, run)
    return code, out.getvalue(), err.getvalue()


class Steps(unittest.TestCase):
    def test_an_unknown_step_a_bad_sha_and_a_relative_toolkit_refuse_before_any_script(self):
        run = FakeRun()
        for argv, environ in (
            (["nothing"], ENVIRON),
            ([], ENVIRON),
            (["name"], {**ENVIRON, deploy_rig.ENV: "abc"}),
            (["name"], {**ENVIRON, "TOOLKIT": "venv"}),
        ):
            code, _, err = step(argv, run, environ)
            self.assertEqual((code, err.startswith("deploy refused: ")), (1, True))
        self.assertEqual(run.calls, [])

    def test_name_answers_the_engines_digest_and_refuses_without_one(self):
        code, out, _ = step(["name"], FakeRun((0, ENVELOPE + "\n")))
        self.assertEqual((code, out.splitlines()[-1]), (0, f"name: {NAME12}"))
        code, _, err = step(["name"], FakeRun((0, "no json\n")))
        self.assertEqual((code, "no build name" in err), (1, True))
        code, _, err = step(["name"], FakeRun((1, "name-error: x\n")))
        self.assertEqual((code, "name exit 1" in err), (1, True))

    def test_install_names_the_build_then_installs_this_package_with_it(self):
        run = FakeRun((0, ENVELOPE), (0, ""))
        self.assertEqual(step(["install"], run)[0], 0)
        (name_script, name_args, _), (install_script, install_args, _) = run.calls
        self.assertEqual((name_script, name_args), (deploy_rig.NAME, (SHA, "/home/abu/venv-decomp")))
        self.assertEqual((install_script, install_args), (deploy_rig.INSTALL, (SHA, "/home/abu/venv-decomp", NAME12)))

    def test_a_failed_install_refuses_with_the_links_unchanged(self):
        code, _, err = step(["install"], FakeRun((0, ENVELOPE), (1, "")))
        self.assertEqual((code, "install exit 1, links unchanged" in err), (1, True))

    def test_installed_reads_the_toolkit_back_against_the_sha_build(self):
        run = FakeRun((0, ""))
        self.assertEqual(step(["installed"], run)[0], 0)
        args = (SHA, "/home/abu/venv-decomp", f"/home/abu/venv-decomp.{SHA}")
        self.assertEqual(run.calls[0][:2], (deploy_rig.INSTALLED, args))
        self.assertEqual(step(["installed"], FakeRun((1, "")))[0], 1)

    def test_oracle_builds_at_the_sha_and_a_failed_build_refuses(self):
        run = FakeRun((0, ""))
        self.assertEqual(step(["oracle"], run)[0], 0)
        self.assertEqual(run.calls[0][:2], (deploy_rig.ORACLE, (SHA,)))
        code, _, err = step(["oracle"], FakeRun((1, "")))
        self.assertEqual((code, "oracle build exit 1, links unchanged" in err), (1, True))

    def test_readback_needs_two_equal_hashes_and_the_live_link_at_the_sha(self):
        self.assertEqual(step(["readback"], FakeRun((0, readback_log(SHA))))[0], 0)
        for log, code in (
            (readback_log(SHA, second="f" * 16), 0),
            (readback_log(NEWER), 0),
            (readback_log(SHA), 1),
            (f"readback-live: {TOOLKIT}.{SHA}", 0),
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
            body = deploy_rig.LIVE.replace(TOOLKIT, str(link))
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

