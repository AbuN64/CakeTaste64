#!/usr/bin/env python3
"""The rig deploy steps of CakeTaste64: one step per `.deploy` line, each an entry point of this file. The close runs
them in file order on the rig at the landed revision, from the root of the tree, with
LANDED_Projects_AbuN64_CakeTaste64 set; it appends its own landing arguments, which no step reads. A step prints its
facts and exits 0, or prints one line on stderr and exits 1; it calls no other tool of the close. CakeTaste64 activates
exactly one link, the live oracle. The decomp toolkit link (/home/abu/venv-decomp) is AbuDecomp64's: this deploy never
activates it, and the oracle recompile imports cake_taste from the tree, never from the toolkit.

  suite      the full test suite (unittest discover over tests/) runs in the tree with its src first on the import path.
             The imported cake_taste path is printed and must lie inside the tree. Any failure refuses (one line naming
             the first failing test) with every live link untouched;
  oracle     the oracle activation (BUILD is <oracle>.<SHA>): builds n64host into BUILD (pinned deps copied from
             ~/src/oracle-deps and patched by hosts/n64recomp/deps.sh) with the build tool at -j"$JOBS". The recompile
             runs first and the decomp names every function: N64Recomp built at its pin with the host's patches, the
             decomp ELF of the PINS RageWars commit (decomp_elf.sh), a taste profile derived for BUILD, `taste recomp
             generate` into BUILD/recomp; oracle-pins.json records that decomp's commit and the sha256 of its
             symbol_addrs.txt, which is kept in BUILD/gen; an address the decomp does not name stops it, listed,
             never a func_ fallback. n64host is configured from that gen dir. Then the oracle self-check runs on that
             build: boot-idle twice, the two state hashes must be equal. Only on exit 0 does the close flip the
             oracle link; a build that is the live target already is a no-op;
  readback   the live oracle link must read <oracle>.<SHA>; its oracle-pins.json must record the PINS RageWars
             commit as the decomp the names came from, the symbol_addrs.txt kept beside the headers must hash to
             the pinned sha256, and the deployed cpu_native.h must be the pinned one, hold no func_ name and name
             osPiStartDma;
             then the self-check again through the live link.

  rig run ci/deploy_rig.py suite
  rig activate ci/deploy_rig.py /home/abu/rig-state/tas/live/oracle oracle
  rig run ci/deploy_rig.py readback"""

import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import PurePosixPath

ENV = "LANDED_Projects_AbuN64_CakeTaste64"
RAGEWARS = "http://10.10.10.130:18556/Projects/RageWars.git"
RAGEWARS_LOCAL = "/home/abu/code/ragewars-decomp"
# The live link of the unbake toolkit (CakeUnbake64's, the one the decomp's own build runs): it writes the clone's
# local.mk. The unbake on PATH is the decomp toolkit's own, which pins an older one.
UNBAKE = "/home/abu/venv-unbake"
RECOMP_PROFILE = "/home/abu/rig-state/behave/taste-profile.json"
VERSION = "us-rev1"
PACKAGE = "cake_taste"
PYTHON = "/usr/bin/python3"
ORACLE_LINK = "/home/abu/rig-state/tas/live/oracle"
SHA = re.compile(r"[0-9a-f]{40}")
HASH = re.compile(r"readback-hash: ([0-9a-f]{16})")

# bash(script, args) runs one of the scripts below and returns its exit code and, when captured, its stdout.
Bash = Callable[..., "tuple[int, str]"]

# selfcheck HOST: boot-idle twice through the oracle binary HOST, one `readback-hash: <first-16-hex>` line per run (env
# as tas_oracle.start); the job fails when the two state hashes differ.
SELFCHECK = r"""selfcheck() {
  host=$1
  movie=$HOME/rig-state/tas/runs/plays/20261030T142016Z-boot-idle/movie.raw
  spec=$HOME/rig-state/tas/runs/state-spec/b1d54eea5d7361537883/oracle.spec
  test -x "$host"; test -f "$movie"; test -f "$spec"
  work=$(mktemp -d)
  first=
  for n in 1 2; do
    out=$work/$n
    mkdir "$out"
    (
      cd "$out"
      export SDL_VIDEODRIVER=offscreen SDL_AUDIODRIVER=dummy
      export N64R_ROM=$HOME/code/ragewars-decomp/roms/baserom.us-rev1.z64 N64R_CONFIG=$HOME/rig-state/tas/oracle/cfg
      export VK_DRIVER_FILES=/usr/share/vulkan/icd.d/lvp_icd.json
      export N64R_PAK=on N64R_PADS=1 N64R_TAS_PLAY=$movie N64R_TAS_EXIT_AFTER=300 N64R_VIEW=$out/view.bmp
      export N64R_AUDIO_OUT=/dev/null N64R_TAS_SHOT_EVERY=0 N64R_TAS_TRACE=$out/oracle-trace.txt
      export N64R_TAS_STATE=$spec N64R_TAS_STATE_HASH=$out/state-oracle.txt
      unset N64R_CPAK
      rc=0
      "$host" > oracle.txt 2>&1 || rc=$?
      test "$rc" = 0 || { tail -n 5 oracle.txt >&2; echo "boot-idle run $n exit $rc" >&2; exit 1; }
    ) || exit 1
    hash=$(sha256sum "$out/state-oracle.txt" | cut -c1-16)
    echo "readback-hash: $hash"
    test -z "$first" || test "$first" = "$hash" || { echo "boot-idle state hashes differ: $first $hash" >&2; exit 1; }
    first=$hash
  done
  rm -rf "$work"
}
"""

# ORACLE: the oracle activation, run in the tree at the SHA with BUILD=<oracle>.<sha> and JOBS set. The build tree and
# copied deps live only while it builds; exit 0 (built and self-checked) is the gate for the flip, and any other exit
# removes $BUILD so the next activation of the sha starts clean. The copied deps are reset to their pins first: the
# shared copy may carry an older patch set, and deps.sh applies this SHA's patches onto the pristine pin.
ORACLE = (
    SELFCHECK
    + r"""set -eu
sha=$(git rev-parse HEAD)
if [ -x "$BUILD/n64host" ] && [ "$(readlink -f "__ORACLE_LINK__")" = "$(readlink -f "$BUILD")" ]; then
  echo "oracle: $BUILD is live already, build skipped"
  exit 0
fi
src=$PWD
mkdir "$BUILD"
trap 'rc=$?; test "$rc" = 0 || rm -rf "$BUILD"' EXIT
mkdir "$BUILD/deps"
cp -a "$HOME/src/oracle-deps/N64ModernRuntime" "$HOME/src/oracle-deps/rt64" "$HOME/src/N64Recomp" "$BUILD/deps/"
rm -rf "$BUILD/deps/N64Recomp/build"
git -C "$BUILD/deps/N64Recomp" reset -q --hard
git -C "$BUILD/deps/N64ModernRuntime" reset -q --hard
git -C "$BUILD/deps/rt64" reset -q --hard
sh hosts/n64recomp/deps.sh "$BUILD/deps"
sh hosts/n64recomp/recompile.sh "$BUILD" "$src" __RAGEWARS__ __RAGEWARS_LOCAL__ __UNBAKE__ \
  __RECOMP_PROFILE__ __VERSION__
gen=$BUILD/recomp/__VERSION__
cmake -S hosts/n64recomp -B "$BUILD/build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=/usr/bin/gcc-14 -DCMAKE_CXX_COMPILER=/usr/bin/g++-14 \
  -DN64R_GEN_DIR="$gen" -DN64R_PINS="$src/hosts/n64recomp/PINS" \
  -DN64R_PROFILE="$HOME/rig-state/behave/oracle-facts.json" \
  -DN64R_REFUSED="$gen/refused.tsv" \
  -DN64R_RT64_SRC="$BUILD/deps/rt64" -DN64R_RUNTIME_SRC="$BUILD/deps/N64ModernRuntime" \
  -DFETCHCONTENT_FULLY_DISCONNECTED=ON
cmake --build "$BUILD/build" -j"$JOBS"
install -m 755 "$BUILD/build/n64host" "$BUILD/n64host"
mkdir "$BUILD/gen"
install -m 644 "$BUILD/build/gen/cpu_profile.h" "$BUILD/build/gen/cpu_native.h" "$gen/symbols.toml" "$BUILD/gen/"
install -m 644 "$BUILD/decomp/versions/__VERSION__/symbol_addrs.txt" "$BUILD/gen/"
python3 - "$sha" "__ORACLE_LINK__/oracle-pins.json" "$BUILD/gen" \
  "$HOME/rig-state/behave/oracle-facts.json" "$(git -C "$BUILD/decomp" rev-parse HEAD)" \
  > "$BUILD/oracle-pins.json" <<'PY'
import hashlib, json, pathlib, sys, time
old = json.load(open(sys.argv[2]))
gen = {n: hashlib.sha256((pathlib.Path(sys.argv[3]) / n).read_bytes()).hexdigest()
       for n in ("cpu_profile.h", "cpu_native.h")}
decomp = {"commit": sys.argv[5],
          "symbol_addrs": hashlib.sha256((pathlib.Path(sys.argv[3]) / "symbol_addrs.txt").read_bytes()).hexdigest()}
print(json.dumps({"cake_taste": sys.argv[1], "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                  "wall": old["wall"], "cpu": old["cpu"], "gen": gen, "decomp": decomp,
                  "facts": hashlib.sha256(pathlib.Path(sys.argv[4]).read_bytes()).hexdigest()}, indent=1))
PY
rm -rf "$BUILD/deps" "$BUILD/build" "$BUILD/decomp" "$BUILD/recomp" "$BUILD/taste-run"
selfcheck "$BUILD/n64host"
"""
).replace("__ORACLE_LINK__", ORACLE_LINK)
for token, value in (
    ("__RAGEWARS_LOCAL__", RAGEWARS_LOCAL),
    ("__UNBAKE__", UNBAKE),
    ("__RAGEWARS__", RAGEWARS),
    ("__RECOMP_PROFILE__", RECOMP_PROFILE),
    ("__VERSION__", VERSION),
):
    ORACLE = ORACLE.replace(token, value)


# live: the live link's resolved target, for the already-live no-op path. A missing link, or one whose target holds no
# executable n64host, exits 1 with the cause on stderr (readlink -f alone answers a path even for a dangling link).
LIVE = f"""link={ORACLE_LINK}
test -L "$link" || {{ echo "live oracle $link is not a symlink" >&2; exit 1; }}
target=$(readlink -f "$link")
test -x "$target/n64host" || {{ echo "live oracle link $link is broken: $target has no n64host" >&2; exit 1; }}
echo "readback-live: $target"
"""

# PINCHECK PINS_JSON PINS GEN: the live oracle's pins against the repository's PINS and the deployed files. The decomp
# commit oracle-pins.json records is the RageWars line of PINS; the symbol_addrs.txt kept in GEN hashes to the sha256 it
# records; the deployed cpu_native.h is the one the pins hash, has no func_, asm_ or entry_ name and names osPiStartDma.
PINCHECK = r"""import hashlib, json, pathlib, re, sys
pins = json.load(open(sys.argv[1]))
want = next(l.split()[1] for l in open(sys.argv[2]) if l.split()[:1] == ["RageWars"])
gen = pathlib.Path(sys.argv[3])
sha = lambda name: hashlib.sha256((gen / name).read_bytes()).hexdigest()
native = (gen / "cpu_native.h").read_text()
unnamed = sorted(set(re.findall(r"(?:func|asm|entry)_[0-9A-Fa-f]{8}\w*", native)))
for ok, message in (
    (pins["decomp"]["commit"] == want, f"the oracle was recompiled from {pins['decomp']['commit']}, PINS has {want}"),
    (sha("symbol_addrs.txt") == pins["decomp"]["symbol_addrs"], "the kept symbol_addrs.txt is not the pinned one"),
    (sha("cpu_native.h") == pins["gen"]["cpu_native.h"], "the deployed cpu_native.h is not the pinned one"),
    (not unnamed, f"cpu_native.h holds {len(unnamed)} unnamed functions: {' '.join(unnamed[:20])}"),
    ('"osPiStartDma"' in native, "cpu_native.h does not name osPiStartDma"),
):
    if not ok:
        sys.exit(f"readback: {message}")
print(f"readback-decomp: {want}")
print(f"readback-symbol-addrs: {pins['decomp']['symbol_addrs']}")
print(f"readback-func-names: {len(unnamed)}")
print("readback-os-pi-start-dma: named")
"""

# readback: the live link's target, the pins check, then the self-check through the live link. Run from the tree root.
READBACK = (
    SELFCHECK
    + f'set -eu\n{LIVE}python3 - "{ORACLE_LINK}/oracle-pins.json" hosts/n64recomp/PINS "{ORACLE_LINK}/gen" <<\'PY\'\n'
    + PINCHECK
    + f'PY\nselfcheck "{ORACLE_LINK}/n64host"\n'
)

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


start = time.monotonic()
runner = unittest.TextTestRunner(stream=sys.stdout, resultclass=Result)
result = runner.run(unittest.defaultTestLoader.discover("tests"))
seconds = time.monotonic() - start
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


def landed(environ: dict[str, str]) -> str:
    """The landed SHA, from its explicit variable."""
    sha = environ.get(ENV, "")
    if not SHA.fullmatch(sha):
        raise Refused(f"{ENV} is not a 40-hex SHA: {sha!r}")
    return sha


def step_suite(sha: str, run: Bash) -> None:
    code, out = run(SUITE.replace("__RUNNER__", RUNNER), PYTHON, capture=True)
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


def step_oracle(sha: str, run: Bash) -> None:
    code, _ = run(ORACLE, sha)
    if code:
        raise Refused(f"oracle build exit {code}, links unchanged")


def step_readback(sha: str, run: Bash) -> None:
    code, out = run(READBACK, capture=True)
    sys.stdout.write(out)
    hashes = HASH.findall(out)
    if code or len(hashes) != 2 or hashes[0] != hashes[1]:
        raise Refused(f"readback exit {code}: {last_line(out, 'no output')}")
    found = facts(out, "readback-")
    if found.get("readback-func-names") != "0" or found.get("readback-os-pi-start-dma") != "named":
        raise Refused(f"readback did not check the deployed names: {last_line(out, 'no output')}")
    live = found.get("readback-live", "")
    if live != f"{ORACLE_LINK}.{sha}":
        raise Refused(f"the live oracle reads {live}, not {ORACLE_LINK}.{sha}")


STEPS = {
    "suite": step_suite,
    "oracle": step_oracle,
    "readback": step_readback,
}


def main(argv: list[str] | None = None, environ: dict[str, str] | None = None, run: Bash = bash) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        step = STEPS.get(argv[0] if argv else "")
        if step is None:
            raise Refused(f"the step is one of {', '.join(STEPS)}, not {argv[0] if argv else 'nothing'!r}")
        step(landed(dict(os.environ) if environ is None else environ), run)
    except Refused as refusal:
        sys.stderr.write(f"deploy refused: {refusal}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
