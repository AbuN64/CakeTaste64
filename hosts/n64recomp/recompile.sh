#!/bin/sh
# recompile.sh BUILD SRC DECOMP PROFILE VERSION PY UNBAKE HOST JOBS: the gen dir BUILD/recomp/VERSION, every function named by
# the decomp.
# Run from the CakeTaste64 checkout SRC after deps.sh put the patched N64Recomp in BUILD/deps. JOBS is the build width;
# PY is the python of the tree's private venv (holds abucache), UNBAKE its unbake command and HOST the host config it is passed; cake_taste comes from SRC.
#   1. N64Recomp and RSPRecomp built at the PINS commit with this host's patches;
#   2. the decomp ELF of the PINS RageWars commit (decomp_elf.sh, cloned from DECOMP with its ROMs, local.mk from
#      UNBAKE with HOST) in BUILD/decomp;
#   3. the PROFILE (the project taste profile) derived for BUILD, and `taste recomp generate` from SRC's own source;
#   4. no func_, asm_ or entry_ name may be left in symbols.toml or funcs/: the unnamed addresses are listed on
#      stderr after `unnamed=<count>` and the script exits 1. Success prints `func_names=0` and the counts.
set -eu
build=${1:?usage: recompile.sh BUILD SRC DECOMP PROFILE VERSION PY UNBAKE HOST JOBS} src=${2:?} decomp=${3:?}
profile=${4:?} version=${5:?} py=${6:?} unbake=${7:?} host=${8:?} jobs=${9:?}
cmake -S "$build/deps/N64Recomp" -B "$build/deps/N64Recomp/build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=/usr/bin/gcc-14 -DCMAKE_CXX_COMPILER=/usr/bin/g++-14
cmake --build "$build/deps/N64Recomp/build" -j"$jobs" --target N64Recomp RSPRecomp
sh "$src/hosts/n64recomp/decomp_elf.sh" "$decomp" "$build/decomp" "$version" "$unbake" "$host" "$jobs"
test -s "$profile" || { echo "recompile: $profile is missing; it is the project taste profile, tracked in this repository (hosts/n64recomp/ragewars.profile.json)" >&2; exit 1; }
mkdir "$build/taste-run"
python3 "$src/hosts/n64recomp/recomp_profile.py" "$profile" "$build" > "$build/taste-profile.json"
PYTHONPATH="$src/src" "$py" -m cake_taste --profile "$build/taste-profile.json" recomp generate --version "$version"
gen=$build/recomp/$version
names='(func|asm|entry)_[0-9A-Fa-f]{8}[A-Za-z0-9_]*'
unnamed=$( (grep -hoE "$names" "$gen/symbols.toml"; grep -rhoE "$names" "$gen/funcs") | sort -u || true)
test -z "$unnamed" || { echo "unnamed=$(echo "$unnamed" | wc -l)" >&2; echo "$unnamed" >&2; exit 1; }
echo "func_names=0"
echo "funcs=$(ls "$gen/funcs" | wc -l) symbols=$(grep -c '{ name = ' "$gen/symbols.toml" || true)"
