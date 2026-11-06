#!/usr/bin/env python3
"""The rig deploy steps of CakeTaste64: one step per `.deploy` line, each an entry point of this file. The close runs
them in file order on the rig at the landed revision, from the root of the tree, with
LANDED_Projects_AbuN64_CakeTaste64 set and `deploy-env TOOLKIT=<live link>` in the environment; it appends its own
landing arguments, which no step reads. A step prints its facts and exits 0, or prints one line on stderr and exits 1;
it calls no other tool of the close. CakeTaste64 activates one link, its dev toolkit (the live link `deploy-env TOOLKIT=` names, which no other
repository names). The oracle build is not here: it is ci/oracle, run on host W through Terminal admission. The toolkit holds every
requirement of pyproject.toml, its `dependencies` (abucache, installed at the commit its `[tool.bootstrap.sources]`
repository holds when the toolkit is built) and the pinned `[dev]` extra, and its one command, `ruff`, is published by name into the one command directory
`commands` beside the toolkit link (the directory the rig job PATH holds); the gate does not call it, it builds its
own venv from `[dev]`. Exactly one toolkit publishes a given name: AbuDecomp64 publishes `decomp` and CakeUnbake64
`unbake` and `unbake-run`, never `ruff`. The decomp toolkit link is AbuDecomp64's and the unbake toolkit
CakeUnbake64's: this deploy never activates them, and nothing of the oracle is deployed.

  suite      the full test suite (unittest discover over tests/) runs in the tree with its src first on the import path,
             under the python of the tree's private venv (ci/ensure_venv.py: every dependency installed, abucache
             included; never the system python, which holds none of them).
             The imported cake_taste path is printed and must lie inside the tree. Any failure refuses (one line naming
             the first failing test) with every live link untouched;
  toolkit    the toolkit activation (BUILD is <toolkit>.<SHA>): a fresh venv at BUILD gets the `dependencies` and the `[dev]`
             extra of the tree's pyproject.toml (every requirement an exact pin or a source at its landed commit) and
             each installed version must equal its pin, each source must read back at its commit.
             Only on exit 0 does the close flip the toolkit link; a failed build removes BUILD, and a build that is the
             live target already is a no-op;
  commands   the live toolkit link must read <toolkit>.<SHA> with every requirement installed. Then each command (`ruff`) is
             linked by name into the command directory, through the live link (so it follows every later switch), and
             read back: it resolves into the live toolkit. The deploy removes nothing.

  deploy-env TOOLKIT=<live link>
  rig run ci/deploy_rig.py suite
  rig activate ci/deploy_rig.py <toolkit live link> toolkit
  rig run ci/deploy_rig.py commands"""

import importlib.util
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path, PurePosixPath

ENV = "LANDED_Projects_AbuN64_CakeTaste64"
COMMANDS = "commands"  # the command directory beside the toolkit link
PUBLISHED = ("ruff",)  # this repository's toolkit command; decomp, unbake and unbake-run are other repositories'
PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;,<>=!~]+)")
SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]* @ git\+\S+@[0-9a-f]{40}")  # a requirement ensure_venv resolved
ENSURE_VENV = "ci/ensure_venv.py"  # the tree's private venv: every dependency of pyproject.toml, abucache included
PACKAGE = "cake_taste"
PYTHON = "/usr/bin/python3"
SHA = re.compile(r"[0-9a-f]{40}")

# bash(script, args) runs one of the scripts below and returns its exit code and, when captured, its stdout.
Bash = Callable[..., "tuple[int, str]"]

# suite PYTHON: the suite run in the Worktree (a clean tree of exactly the SHA), one `suite-KEY: value` line per fact.
SUITE = r"""set -u
py=$1
runner=$(mktemp)
echo "suite-checkout: $(pwd -P)"
echo "suite-sha: $(git rev-parse HEAD)"
cat > "$runner" <<'PY'
__RUNNER__
PY
mkdir .tmp
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=init.templateDir GIT_CONFIG_VALUE_0=
export TMPDIR=$PWD/.tmp PYTHONPATH=$PWD/src PYTHONDONTWRITEBYTECODE=1
rc=0
"$py" "$runner" || rc=$?
rm -rf "$runner" .tmp
exit "$rc"
"""

# The runner: puts the checkout's src first, proves the package comes from there, runs unittest discover over tests/.
RUNNER = r"""import os
import sys
import time
import unittest

root = os.path.realpath(os.getcwd())
sys.path.insert(0, os.path.join(root, "src"))
PACKAGE = "cake_taste"


def emit(line):
    # a test may leave its own output without a newline: every fact starts on a fresh line
    sys.stdout.write("\n" + line + "\n")
    sys.stdout.flush()


def imported():
    return sorted({os.path.realpath(m.__file__) for n, m in sys.modules.items()
                   if (n == PACKAGE or n.startswith(PACKAGE + ".")) and getattr(m, "__file__", None)})


def inside(path):
    return path.startswith(root + os.sep)


__import__(PACKAGE)
emit("suite-import: " + os.path.realpath(sys.modules[PACKAGE].__file__))


class Result(unittest.TextTestResult):
    first = None

    def addFailure(self, test, err):
        self.first = self.first or test.id()
        super().addFailure(test, err)

    def addError(self, test, err):
        self.first = self.first or test.id()
        super().addError(test, err)

    def addUnexpectedSuccess(self, test):
        self.first = self.first or test.id()
        super().addUnexpectedSuccess(test)


from abucache import stages
with stages.timed("ci.suite") as elapsed:
    runner = unittest.TextTestRunner(stream=sys.stdout, resultclass=Result)
    result = runner.run(unittest.defaultTestLoader.discover("tests"))
seconds = elapsed.seconds
outside = [path for path in imported() if not inside(path)]
if outside:
    emit("suite-import: " + outside[0])
    sys.exit(3)
failed = len(result.failures) + len(result.errors) + len(result.unexpectedSuccesses)
passed = result.testsRun - failed - len(result.skipped)
emit(f"suite-summary: {passed} passed, {failed} failed, {len(result.skipped)} skipped in {seconds:.0f} s")
if failed or not passed:
    emit("suite-first-failure: " + (result.first or f"{passed} tests passed"))
    sys.exit(1)
"""


# PINCHECK_TOOLKIT REQUIREMENT...: run by a toolkit's own python, each requirement installed, one `toolkit-pin: name
# version` line per requirement. `name==version` must be installed at exactly that version, `name @ git+URL@SHA` at
# exactly that commit, and a bare `name` only installed.
PINCHECK_TOOLKIT = r"""import importlib.metadata
import json
import re
import sys

for requirement in sys.argv[1:]:
    found = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:==(\S+)| @ git\+\S+@([0-9a-f]{40}))?", requirement)
    if not found:
        sys.exit(f"toolkit: {requirement!r} is not name, name==version or name @ git+URL@SHA")
    name, pinned, commit = found.groups()
    installed = importlib.metadata.version(name)
    print(f"toolkit-pin: {name} {installed}")
    if pinned and installed != pinned:
        sys.exit(f"toolkit: {name} is {installed}, pyproject pins {pinned}")
    if commit:
        direct = json.loads(importlib.metadata.distribution(name).read_text("direct_url.json") or "{}")
        at = direct.get("vcs_info", {}).get("commit_id")
        if at != commit:
            sys.exit(f"toolkit: {name} is at {at}, its source resolved to {commit}")
"""

# TOOLKIT_BUILD TOOLKIT REQUIREMENT...: the toolkit activation, run in the tree with BUILD (<toolkit>.<sha>) set by the
# close. BUILD itself is the fresh venv (the python3 of the job), the pinned requirements are installed into it and each
# is read back; exit 0 is the gate for the close's flip and any other exit removes BUILD so the next activation of the
# sha starts clean. A BUILD that is the live toolkit already is a no-op.
TOOLKIT_BUILD = (
    r"""set -u
toolkit=$1
shift
: "${BUILD:?the close sets BUILD for an activation}"
if [ -x "$BUILD/bin/python" ] && [ "$(readlink -f "$toolkit")" = "$(readlink -f "$BUILD")" ]; then
  echo "toolkit: $BUILD is live already, build skipped"
  exit 0
fi
[ -e "$BUILD" ] && { echo "deploy-error: $BUILD exists" >&2; exit 1; }
trap 'rc=$?; test "$rc" = 0 || rm -rf "$BUILD"' EXIT
python3 -m venv "$BUILD" || { echo "deploy-error: venv exit $?" >&2; exit 1; }
"$BUILD/bin/python" -m pip install -q "$@" || { echo "deploy-error: pip install exit $?" >&2; exit 1; }
"$BUILD/bin/python" - "$@" <<'PY' || exit 1
"""
    + PINCHECK_TOOLKIT
    + r"""PY
echo "toolkit-build: $BUILD"
"""
)

# TOOLKIT_READBACK TOOLKIT BUILD REQUIREMENT...: the live toolkit link reads BUILD and holds every pin through the link.
TOOLKIT_READBACK = (
    r"""set -u
toolkit=$1 build=$2
shift 2
[ "$(readlink -f "$toolkit")" = "$(readlink -f "$build")" ] \
  || { echo "toolkit: $toolkit reads $(readlink -f "$toolkit"), not the build $build" >&2; exit 1; }
"$toolkit/bin/python" - "$@" <<'PY' || exit 1
"""
    + PINCHECK_TOOLKIT
    + r"""PY
"""
)


class Refused(Exception):
    """One line cause of a refusal or failed step."""


def bash(script: str, *args: str, capture: bool = False) -> tuple[int, str]:
    done = subprocess.run(
        ["bash", "-c", script, "step", *args], stdout=subprocess.PIPE if capture else None, text=True, check=False
    )
    return done.returncode, done.stdout or ""


def last_line(text: str, default: object) -> str:
    return text.strip().splitlines()[-1] if text.strip() else str(default)


def facts(log: str, prefix: str) -> dict[str, str]:
    found = {}
    for line in log.splitlines():
        key, sep, value = line.partition(": ")
        if sep and key.startswith(prefix):
            found[key] = value
    return found


def landed(environ: dict[str, str]) -> tuple[str, str]:
    """The landed SHA, from its explicit variable, and the live toolkit link, from `deploy-env TOOLKIT=`."""
    sha = environ.get(ENV, "")
    if not SHA.fullmatch(sha):
        raise Refused(f"{ENV} is not a 40-hex SHA: {sha!r}")
    toolkit = environ.get("TOOLKIT", "")
    if not toolkit.startswith("/"):
        raise Refused(f"TOOLKIT is not an absolute path (a `deploy-env TOOLKIT=<live link>` line): {toolkit!r}")
    return sha, toolkit


def commands(toolkit: str) -> str:
    """The one command directory, beside the live toolkit link: the folder the rig job PATH holds."""
    return os.path.join(os.path.dirname(toolkit), COMMANDS)


def toolkit_requirements(root: Path, lookup: Callable | None = None) -> list[str]:
    """Every requirement the toolkit installs: the `dependencies` and the `[dev]` extra of the tree's pyproject.toml,
    each source dependency resolved by ci/ensure_venv.py to its landed commit and every other entry an exact pin."""
    spec = importlib.util.spec_from_file_location("ensure_venv", root / ENSURE_VENV)
    if spec is None or spec.loader is None:
        raise Refused(f"{ENSURE_VENV} is missing in {root}")
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    try:
        found = bootstrap.requirements(root, lookup, ("dev",))
    except RuntimeError as failure:
        raise Refused(str(failure)) from failure
    loose = [row for row in found if not (PIN.fullmatch(row) or SOURCE.fullmatch(row))]
    if loose or not found:
        raise Refused(f"pyproject.toml must pin every requirement with == or give it a source: {loose or 'it is empty'}")
    return found


def installed_requirements(root: Path) -> list[str]:
    """The same list without a network lookup, for the readback: a pin as pinned, a source dependency by its name."""
    with open(root / "pyproject.toml", "rb") as handle:
        project = tomllib.load(handle).get("project", {})
    rows = [*project.get("dependencies", []), *project.get("optional-dependencies", {}).get("dev", [])]
    return [row if PIN.fullmatch(row) else re.split(r"[\s=<>!~;@\[]", row, maxsplit=1)[0] for row in rows]


def publish(toolkit: str) -> list[str]:
    """Link each command of this repository into the command directory by name, through the live toolkit link, and
    read each back: it must resolve into the live toolkit. Returns the names published."""
    folder = commands(toolkit)
    os.makedirs(folder, exist_ok=True)
    live = os.path.realpath(toolkit)
    for command in PUBLISHED:
        link = os.path.join(folder, command)
        tmp = f"{link}.{os.getpid()}.new"
        os.symlink(os.path.join(toolkit, "bin", command), tmp)
        os.replace(tmp, link)
        if os.path.commonpath([os.path.realpath(link), live]) != live:
            raise Refused(f"{link} resolves to {os.path.realpath(link)}, outside the live toolkit {live}")
    return list(PUBLISHED)


def step_suite(sha: str, toolkit: str, run: Bash, environ: dict[str, str]) -> None:
    code, out = run(r'exec "$1" "$2"', PYTHON, ENSURE_VENV, capture=True)
    python = last_line(out, "")
    if code or not python.startswith("/"):
        raise Refused(f"suite environment ({ENSURE_VENV}) exit {code}: {python or 'no output'}")
    code, out = run(SUITE.replace("__RUNNER__", RUNNER), python, capture=True)
    sys.stdout.write(out)
    found = facts(out, "suite-")
    for key in ("suite-checkout", "suite-sha", "suite-import"):
        if key not in found:
            raise Refused(f"suite did not start at {sha[:12]} (exit {code}): {last_line(out, 'no output')}")
    if found["suite-sha"] != sha:
        raise Refused(f"suite ran at {found['suite-sha']}, not the landed {sha}")
    if not PurePosixPath(found["suite-import"]).is_relative_to(found["suite-checkout"]):
        raise Refused(
            f"suite imported {PACKAGE} from {found['suite-import']}, outside its checkout {found['suite-checkout']}"
        )
    if code or "suite-first-failure" in found or "suite-summary" not in found:
        first = found.get("suite-first-failure") or f"no summary, exit {code}: {last_line(out, 'no output')}"
        raise Refused(f"suite failed at {first}: {found.get('suite-summary', 'no summary')}")


def step_toolkit(sha: str, toolkit: str, run: Bash, environ: dict[str, str]) -> None:
    code, _ = run(TOOLKIT_BUILD, toolkit, *toolkit_requirements(Path.cwd()))
    if code:
        raise Refused(f"toolkit build exit {code}, links unchanged")


def step_commands(sha: str, toolkit: str, run: Bash, environ: dict[str, str]) -> None:
    code, out = run(TOOLKIT_READBACK, toolkit, f"{toolkit}.{sha}", *installed_requirements(Path.cwd()), capture=True)
    sys.stdout.write(out)
    if code:
        raise Refused(f"toolkit readback exit {code}: {last_line(out, 'no output')}")
    sys.stdout.write(f"publish: {', '.join(publish(toolkit))} -> {commands(toolkit)}\n")


STEPS = {
    "suite": step_suite,
    "toolkit": step_toolkit,
    "commands": step_commands,
}


def main(argv: list[str] | None = None, environ: dict[str, str] | None = None, run: Bash = bash) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        step = STEPS.get(argv[0] if argv else "")
        if step is None:
            raise Refused(f"the step is one of {', '.join(STEPS)}, not {argv[0] if argv else 'nothing'!r}")
        environ = dict(os.environ) if environ is None else environ
        step(*landed(environ), run, environ)
    except Refused as refusal:
        sys.stderr.write(f"deploy refused: {refusal}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
