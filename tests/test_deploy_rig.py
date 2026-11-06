"""The rig deploy steps: each `.deploy` step run through a fake bash seam; the suite, live-link and runner scripts run
for real in a tiny repository or a temp link."""

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
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ci/deploy_rig.py"
ENSURE = "ci/ensure_venv.py"
spec = importlib.util.spec_from_file_location("deploy_rig", SCRIPT)
deploy_rig = importlib.util.module_from_spec(spec)
sys.modules["deploy_rig"] = deploy_rig
spec.loader.exec_module(deploy_rig)

SHA = "7a6ab5ce4712939ea310ee62e2fbc759920f6363"
NEWER = "c" * 40
HASH = "63576cbe05149fa7"
SITE = f"{tempfile.gettempdir()}/example"  # a mocked rig home: no host path is tracked
CHECKOUT = f"{SITE}/runs/b.c/Projects/AbuN64/CakeTaste64"
TOOLKIT = f"{SITE}/venv-taste"  # the step takes it from its environment: any absolute path


def suite_log(sha=SHA, imported=f"{CHECKOUT}/src/cake_taste/__init__.py", failed=None):
    lines = [f"suite-checkout: {CHECKOUT}", f"suite-sha: {sha}", f"suite-import: {imported}"]
    if failed:
        lines += ["suite-summary: 1766 passed, 1 failed, 5 skipped in 204 s", f"suite-first-failure: {failed}"]
    else:
        lines.append("suite-summary: 1767 passed, 0 failed, 5 skipped in 204 s")
    return "\n".join(lines)




RUNTIME_DEFINES = ("osViGetCurrentFramebuffer", "osViBlack", "osSpTaskStartGo", "osSpTaskYield", "osVirtualToPhysical",
                   "osDpSetNextBuffer", "osPiStartDma")


def write_symbol_lists(build: Path) -> None:
    """N64Recomp's symbol_lists.cpp as deps.sh leaves it: the reimplemented block, then the ignored one."""
    (build / "deps/N64Recomp/src").mkdir(parents=True)
    names = "".join(f'    "{n}",\n' for n in RUNTIME_DEFINES)
    (build / "deps/N64Recomp/src/symbol_lists.cpp").write_text(
        f"const std::unordered_set<std::string> N64Recomp::reimplemented_funcs {{\n{names}}};\n"
        'const std::unordered_set<std::string> N64Recomp::ignored_funcs {\n    "__osViInit",\n};\n')






class Shape(unittest.TestCase):
    def test_deploy_file_is_one_step_grammar_line_per_step_and_the_script_is_executable(self):
        lines = (ROOT / ".deploy").read_text().splitlines()
        self.assertEqual(lines[:1], ["deploy-input TOOLKIT"])
        self.assertNotIn("deploy-env", "\n".join(lines))
        self.assertNotIn("/home", "\n".join(lines))
        lines = lines[1:]
        steps = [line.split()[-1] for line in lines]
        self.assertEqual(steps, ["suite", "toolkit", "commands"])
        self.assertEqual(steps, list(deploy_rig.STEPS))
        prefixes = ("rig run ci/deploy_rig.py ", "rig activate ci/deploy_rig.py $")
        self.assertTrue(all(line.startswith(prefixes) for line in lines))
        self.assertTrue((ROOT / "ci/deploy_rig.py").stat().st_mode & stat.S_IXUSR)
        self.assertTrue(SCRIPT.read_text().startswith("#!/usr/bin/env python3\n"))

    def test_scripts_parse_and_no_commit_pin_or_job_invoke_remains(self):
        scripts = (
            deploy_rig.SUITE,
            deploy_rig.TOOLKIT_BUILD,
            deploy_rig.TOOLKIT_READBACK,
        )
        for body in scripts:
            self.assertEqual(subprocess.run(["bash", "-n"], input=body, text=True, check=False).returncode, 0)
        source = SCRIPT.read_text()
        for retired in ("finish/", "flock", "waitfor", "ssh", "-j8", "Job Invoke", "butlermaster"):
            self.assertNotIn(retired, source)


    def test_this_deploy_publishes_ruff_and_no_command_another_toolkit_publishes(self):
        self.assertEqual(deploy_rig.PUBLISHED, ("ruff",))
        for taken in ("decomp", "unbake", "unbake-run"):
            self.assertNotIn(taken, deploy_rig.PUBLISHED)
        gate = (ROOT / "ci/check").read_text()
        self.assertIn('\n"$py" -m ruff check src tests ci/ensure_venv.py\n', gate)
        self.assertIn('py="$(python3 ci/ensure_venv.py)"', gate)
        self.assertNotIn("\nruff ", gate)




    def test_recompile_sh_runs_its_steps_in_order_and_stops_on_an_unnamed_address(self):
        text = (ROOT / "hosts/n64recomp/recompile.sh").read_text()
        steps = ['cmake --build "$build/deps/N64Recomp/build"', 'sh "$src/hosts/n64recomp/decomp_elf.sh"',
                 'python3 "$src/hosts/n64recomp/recomp_profile.py"', '"$py" -m cake_taste', 'test -z "$unnamed"',
                 'echo "func_names=0"']
        at = [text.index(step) for step in steps]
        self.assertEqual(at, sorted(at))
        self.assertIn('echo "$unnamed" >&2; exit 1', text)
        self.assertIn('py=${6:?}', text)  # the private venv python holds abucache; the system python3 holds none
        self.assertNotIn("TOOLKIT", text)
        self.assertNotIn("python3 -m cake_taste", text)
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


class ToolkitPinCheck(unittest.TestCase):
    """PINCHECK_TOOLKIT run for real by a python that sees one fake installed distribution built from a git source."""

    def check(self, *requirements):
        with tempfile.TemporaryDirectory() as raw:
            info = Path(raw) / "fakecache-1.2.dist-info"
            info.mkdir()
            (info / "METADATA").write_text("Metadata-Version: 2.1\nName: fakecache\nVersion: 1.2\n")
            (info / "direct_url.json").write_text(
                json.dumps({"url": "https://example.lan/c", "vcs_info": {"vcs": "git", "commit_id": SHA}})
            )
            done = subprocess.run(
                [sys.executable, "-c", deploy_rig.PINCHECK_TOOLKIT, *requirements],
                env={"PYTHONPATH": raw}, capture_output=True, text=True, check=False,
            )
        return done.returncode, done.stdout + done.stderr

    def test_a_pin_a_source_commit_and_a_bare_name_are_each_read_back(self):
        source = f"fakecache @ git+https://example.lan/c@{SHA}"
        code, out = self.check("fakecache==1.2", source, "fakecache")
        self.assertEqual((code, out.count("toolkit-pin: fakecache 1.2")), (0, 3), out)
        for requirement, needle in (
            ("fakecache==1.3", "pyproject pins 1.3"),
            (f"fakecache @ git+https://example.lan/c@{NEWER}", "its source resolved to"),
            ("nosuchdist", "nosuchdist"),
            ("fakecache>=1", "is not name"),
        ):
            code, out = self.check(requirement)
            self.assertEqual((code != 0, needle in out), (True, True), (requirement, out))


class FakeRun:
    """The bash seam: answers each script with the next scripted (exit code, stdout) and records the calls."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, script, *args, capture=False):
        self.calls.append((script, args, capture))
        return self.answers.pop(0)


ENVIRON = {deploy_rig.ENV: SHA, "TOOLKIT": TOOLKIT, "ORACLE_DECOMP": "/decomp", "ORACLE_DEPS": "/deps"}


def step(argv, run, environ=ENVIRON):
    with redirect_stdout(StringIO()) as out, redirect_stderr(StringIO()) as err:
        code = deploy_rig.main(argv, environ, run)
    return code, out.getvalue(), err.getvalue()


class Host:
    """A throwaway rig home: the toolkit link, the command directory beside it."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        self.toolkit = str(home / "venv-taste")
        self.commands = home / "commands"
        self.commands.mkdir()
        live = home / f"venv-taste.{SHA}"
        (live / "bin").mkdir(parents=True)
        (live / "bin/ruff").write_text("#!/bin/sh\n")
        Path(self.toolkit).symlink_to(live)
        self.environ = {**ENVIRON, "TOOLKIT": self.toolkit}
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


    def test_toolkit_installs_the_dependencies_and_pinned_dev_extra_of_the_tree_and_a_failed_build_refuses(self):
        rows = [f"abucache @ git+https://example.lan/cache-64@{SHA}", "ruff==0.16.9"]
        with mock.patch.object(deploy_rig, "toolkit_requirements", return_value=rows):
            run = FakeRun((0, ""))
            self.assertEqual(step(["toolkit"], run)[0], 0)
            self.assertEqual(run.calls[0][:2], (deploy_rig.TOOLKIT_BUILD, (TOOLKIT, *rows)))
            code, _, err = step(["toolkit"], FakeRun((1, "")))
        self.assertEqual((code, "toolkit build exit 1, links unchanged" in err), (1, True))

    def test_the_toolkit_requirements_are_the_dependencies_resolved_to_their_commit_and_the_dev_pins(self):
        seen = []

        def lookup(argv, cwd):
            seen.append(argv)
            return 0, SHA + "\tHEAD\n"

        rows = deploy_rig.toolkit_requirements(ROOT, lookup)
        self.assertEqual(rows[0], f"abucache @ git+{seen[0][2]}@{SHA}")
        self.assertEqual(rows[1:], ["ruff==0.16.9"])
        self.assertEqual(deploy_rig.installed_requirements(ROOT), ["abucache", "ruff==0.16.9"])
        with tempfile.TemporaryDirectory() as raw:
            tree = Path(raw)
            (tree / "ci").mkdir()
            (tree / "ci/ensure_venv.py").write_bytes((ROOT / ENSURE).read_bytes())
            (tree / "pyproject.toml").write_text(
                '[project]\ndependencies = ["requests>=2"]\n[project.optional-dependencies]\ndev = ["ruff==1"]\n'
                "[tool.bootstrap.sources]\n"
            )
            with self.assertRaisesRegex(deploy_rig.Refused, "requests>=2"):
                deploy_rig.toolkit_requirements(tree, lookup)
            with self.assertRaisesRegex(deploy_rig.Refused, "ls-remote"):
                deploy_rig.toolkit_requirements(ROOT, lambda argv, cwd: (128, "offline"))

    def test_commands_reads_the_toolkit_back_then_publishes_ruff_by_name(self):
        with Host() as host:
            run = FakeRun((0, "toolkit-pin: ruff 0.16.9\n"))
            code, out, _ = step(["commands"], run, host.environ)
            self.assertEqual(code, 0, out)
            args = (host.toolkit, f"{host.toolkit}.{SHA}", "abucache", "ruff==0.16.9")
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


    def test_the_suite_runs_under_the_python_of_the_trees_private_venv_never_the_system_python(self):
        venv = f"{CHECKOUT}/.venv/bin/python"
        run = FakeRun((0, f"{venv}\n"), (0, suite_log()))
        self.assertEqual(step(["suite"], run)[0], 0)
        self.assertEqual(run.calls[0][1], (deploy_rig.PYTHON, deploy_rig.ENSURE_VENV))
        self.assertEqual(run.calls[1][1], (venv,))
        for answer in ((1, ""), (0, ""), (1, "venv: pip failed")):
            run = FakeRun(answer)
            code, _, err = step(["suite"], run)
            self.assertEqual((code, "suite environment" in err, len(run.calls)), (1, True, 1), err)

    def test_the_suite_step_accepts_a_clean_suite_and_names_every_refusal(self):
        self.assertEqual(step(["suite"], FakeRun((0, "/venv/bin/python"), (0, suite_log())))[0], 0)
        cases = (
            ((1, suite_log(failed="test_x (t.T)")), "test_x (t.T)"),
            ((0, suite_log(imported="/elsewhere/cake_taste/__init__.py")), "outside its checkout"),
            ((0, suite_log(imported=f"{CHECKOUT}-sibling/src/cake_taste/__init__.py")), "outside its checkout"),
            ((0, suite_log(sha=NEWER)), "not the landed"),
            ((1, "boom"), "did not start"),
            ((1, suite_log().replace("suite-summary", "x")), "no summary"),
        )
        for answer, needle in cases:
            code, _, err = step(["suite"], FakeRun((0, "/venv/bin/python"), answer))
            self.assertEqual((code, needle in err), (1, True), (answer, err))






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

