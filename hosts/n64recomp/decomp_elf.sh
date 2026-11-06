#!/bin/sh
# decomp_elf.sh DECOMP DEST VERSION UNBAKE HOST JOBS: the RageWars decomp at the PINS commit in DEST (a fresh clone of the sibling working copy DECOMP), linked for
# VERSION:
# DEST/build/VERSION/RageWars.elf and DEST/versions/VERSION/symbol_addrs.txt come from that commit and nothing else.
# Only the cartridge dumps (roms/) are taken from DECOMP, the sibling decomp working copy; no source, symbol
# file, build output or local.mk is. local.mk is written in DEST by the UNBAKE command, the repo's declared toolchain
# (`unbake --host HOST setup --no-make`, HOST being the tree's own generated host config, the way the decomp's own rig build prepares its tree), so its tool paths and
# object-store command belong to the toolkit that generates the Makefile; a copied local.mk names whatever toolkit last
# wrote the working copy.
set -eu
decomp=${1:?usage: decomp_elf.sh DECOMP DEST VERSION UNBAKE HOST JOBS} dest=${2:?} version=${3:?} unbake=${4:?} host=${5:?} jobs=${6:?}
here=$(cd "$(dirname "$0")" && pwd)
pin=$(awk '$1 == "RageWars" { print $2 }' "$here/PINS")
test -n "$pin" || { echo "decomp_elf: PINS has no RageWars line" >&2; exit 1; }
if [ ! -d "$dest/.git" ]; then  # made beside DEST and renamed, so a kill leaves no half-made DEST; a finished DEST is kept and make resumes
  rm -rf "$dest.clone"
  git clone -q --no-checkout "$decomp" "$dest.clone"
  git -C "$dest.clone" checkout -q --detach "$pin"
  cp -a "$decomp/roms" "$dest.clone/roms"
  mv "$dest.clone" "$dest"
fi
test "$(git -C "$dest" rev-parse HEAD)" = "$pin"
# The selected tool owns both generated files. Setup regenerates an absent Makefile, including the
# four-argument keep_dates calls, alongside its matching local.mk; never retain the pinned tree's old generator output.
"$(dirname "$unbake")/python" "$here/refresh_makefile.py" "$dest/Makefile"
(cd "$dest" && "$unbake" --host "$host" setup --no-make)
test -s "$dest/local.mk"
make -C "$dest" -j"$jobs" "build/$version/RageWars.elf"
test -s "$dest/build/$version/RageWars.elf"
echo "decomp_elf: $pin $version"
