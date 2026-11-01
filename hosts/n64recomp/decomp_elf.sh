#!/bin/sh
# decomp_elf.sh URL DEST VERSION UNBAKE: the RageWars decomp at the PINS commit in DEST (a fresh clone), linked for
# VERSION:
# DEST/build/VERSION/RageWars.elf and DEST/versions/VERSION/symbol_addrs.txt come from that commit and nothing else.
# Only the cartridge dumps (roms/) are taken from $RAGEWARS_LOCAL, the rig's decomp working copy; no source, symbol
# file, build output or local.mk is. local.mk is written in DEST by the unbake toolkit UNBAKE (its live link:
# `UNBAKE/bin/unbake setup --no-make`, the way the decomp's own rig build prepares its tree), so its tool paths and
# object-store command belong to the toolkit that generates the Makefile; a copied local.mk names whatever toolkit last
# wrote the working copy.
set -eu
url=${1:?usage: decomp_elf.sh URL DEST VERSION UNBAKE} dest=${2:?} version=${3:?} unbake=${4:?}
here=$(cd "$(dirname "$0")" && pwd)
pin=$(awk '$1 == "RageWars" { print $2 }' "$here/PINS")
test -n "$pin" || { echo "decomp_elf: PINS has no RageWars line" >&2; exit 1; }
local_dir=${RAGEWARS_LOCAL:?RAGEWARS_LOCAL names the decomp working copy that holds roms/}
git clone -q --no-checkout "$url" "$dest"
git -C "$dest" checkout -q --detach "$pin"
test "$(git -C "$dest" rev-parse HEAD)" = "$pin"
cp -a "$local_dir/roms" "$dest/roms"
(cd "$dest" && "$unbake/bin/unbake" setup --no-make)
test -s "$dest/local.mk"
make -C "$dest" -j"${JOBS:?JOBS is set by Job Invoke}" "build/$version/RageWars.elf"
test -s "$dest/build/$version/RageWars.elf"
echo "decomp_elf: $pin $version"
