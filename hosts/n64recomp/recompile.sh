#!/bin/sh
# recompile.sh BUILD SRC DECOMP PROFILE VERSION ROM PY UNBAKE HOST JOBS DEPS [CMAKE_ARG...]: the gen dir BUILD/recomp/VERSION, every function named by
# the decomp.
# Run from the CakeTaste64 checkout SRC after deps.sh put the patched N64Recomp in DEPS (BUILD/deps links to it; the N64Recomp
# build dir DEPS/N64Recomp/build stays between runs, so ninja resumes). Rerunnable on a BUILD a kill left half made: a finished
# piece (the decomp ELF clone, the object files of either build) is kept, the generated tree is made again. The CMAKE_ARGs
# (the compiler launcher) go to the N64Recomp configure. JOBS is the build width;
# ROM is the cartridge dump of VERSION in DECOMP (found once by ci/oracle, handed on: the derived profile names it, never a path
# beside the profile file, which is the worktree's); PY is the python of the tree's private venv (holds abucache), UNBAKE its unbake command and HOST the host config it is passed; cake_taste comes from SRC.
#   1. N64Recomp and RSPRecomp built at the PINS commit with this host's patches;
#   2. the decomp ELF of the PINS RageWars commit (decomp_elf.sh, cloned from DECOMP with its ROMs, local.mk from
#      UNBAKE with HOST) in BUILD/decomp;
#   3. the PROFILE (the project taste profile) derived for BUILD, and `taste recomp generate` from SRC's own source;
#   4. no func_, asm_ or entry_ name may be left in symbols.toml or funcs/: the unnamed addresses are listed on
#      stderr after `unnamed=<count>` and the script exits 1. Success prints `func_names=0` and the counts.
set -eu
build=${1:?usage: recompile.sh BUILD SRC DECOMP PROFILE VERSION ROM PY UNBAKE HOST JOBS DEPS [CMAKE_ARG...]} src=${2:?} decomp=${3:?}
profile=${4:?} version=${5:?} rom=${6:?} py=${7:?} unbake=${8:?} host=${9:?} jobs=${10:?} deps=${11:?}
shift 11
ln -sfn "$deps" "$build/deps"
cmake -S "$deps/N64Recomp" -B "$deps/N64Recomp/build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=/usr/bin/gcc-14 -DCMAKE_CXX_COMPILER=/usr/bin/g++-14 "$@"
cmake --build "$deps/N64Recomp/build" -j"$jobs" --target N64Recomp RSPRecomp
sh "$src/hosts/n64recomp/decomp_elf.sh" "$decomp" "$build/decomp" "$version" "$unbake" "$host" "$jobs"
test -s "$profile" || { echo "recompile: $profile is missing; it is the project taste profile, tracked in this repository (hosts/n64recomp/ragewars.profile.json)" >&2; exit 1; }
rm -rf "$build/taste-run" "$build/recomp"
mkdir "$build/taste-run"
python3 "$src/hosts/n64recomp/recomp_profile.py" "$profile" "$build" "$version" "$rom" > "$build/taste-profile.json"
PYTHONPATH="$src/src" "$py" -m cake_taste --profile "$build/taste-profile.json" recomp generate --version "$version"
gen=$build/recomp/$version
names='(func|asm|entry)_[0-9A-Fa-f]{8}[A-Za-z0-9_]*'
unnamed=$( (grep -hoE "$names" "$gen/symbols.toml"; grep -rhoE "$names" "$gen/funcs") | sort -u || true)
test -z "$unnamed" || { echo "unnamed=$(echo "$unnamed" | wc -l)" >&2; echo "$unnamed" >&2; exit 1; }
echo "func_names=0"
echo "funcs=$(ls "$gen/funcs" | wc -l) symbols=$(grep -c '{ name = ' "$gen/symbols.toml" || true)"
