#!/bin/sh
# decomp_elf.sh URL DEST VERSION: the RageWars decomp at the PINS commit in DEST (a fresh clone), linked for VERSION:
# DEST/build/VERSION/RageWars.elf and DEST/versions/VERSION/symbol_addrs.txt come from that commit and nothing else.
# The machine-local build setup (local.mk with the tool paths, the dumps in roms/) is the only thing taken from
# $RAGEWARS_LOCAL, the rig's decomp working copy; no source, symbol file or build output is.
set -eu
url=${1:?usage: decomp_elf.sh URL DEST VERSION} dest=${2:?} version=${3:?}
here=$(cd "$(dirname "$0")" && pwd)
pin=$(awk '$1 == "RageWars" { print $2 }' "$here/PINS")
test -n "$pin" || { echo "decomp_elf: PINS has no RageWars line" >&2; exit 1; }
local_dir=${RAGEWARS_LOCAL:?RAGEWARS_LOCAL names the decomp working copy that holds local.mk and roms/}
git clone -q --no-checkout "$url" "$dest"
git -C "$dest" checkout -q --detach "$pin"
test "$(git -C "$dest" rev-parse HEAD)" = "$pin"
cp "$local_dir/local.mk" "$dest/local.mk"
cp -a "$local_dir/roms" "$dest/roms"
make -C "$dest" -j"${JOBS:?JOBS is set by Job Invoke}" "build/$version/RageWars.elf"
test -s "$dest/build/$version/RageWars.elf"
echo "decomp_elf: $pin $version"
