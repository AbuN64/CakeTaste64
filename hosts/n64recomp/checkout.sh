#!/bin/sh
# checkout.sh DIR URL PIN: DIR is a clone of URL at commit PIN with every nested submodule checked out. Rerunnable after
# a kill at any point: git marks a submodule clone in progress with HEAD "ref: refs/heads/.invalid" and later reruns
# take that half-made module for a finished one ("fatal: Unable to find current revision in submodule path"), so the
# modules still marked (and their working copies) are dropped before the update and fetched again; the main clone is
# made beside DIR and renamed, so DIR is never a half-made clone.
set -eu
dir=${1:?usage: checkout.sh DIR URL PIN} url=${2:?usage: checkout.sh DIR URL PIN} pin=${3:?usage: checkout.sh DIR URL PIN}
if [ ! -d "$dir/.git" ]; then
  rm -rf "$dir.clone"
  git clone -q "$url" "$dir.clone"
  mv "$dir.clone" "$dir"
fi
if [ "$(git -C "$dir" rev-parse HEAD)" != "$pin" ]; then
  git -C "$dir" diff --quiet || { echo "deps: $dir has edits and is not at $pin" >&2; exit 1; }
  git -C "$dir" fetch -q origin
  git -C "$dir" checkout -q --detach "$pin"
fi
unfinished() {  # the gitdir $1 is a submodule clone that was killed
  grep -qx 'ref: refs/heads/.invalid' "$1/HEAD" 2>/dev/null
}
find "$dir" -name .git -type f | while read -r gitfile; do  # a submodule working copy
  wt=$(dirname "$gitfile")
  gd=$(cd "$wt" && cd "$(sed 's/^gitdir: //' .git)" 2>/dev/null && pwd) || gd=
  if [ -z "$gd" ] || unfinished "$gd"; then
    test -z "$gd" || rm -rf "$gd"
    rm -rf "$wt"
    mkdir "$wt"
  fi
done
find "$dir/.git" -name HEAD -type f | while read -r head; do  # a module without a working copy
  ! unfinished "$(dirname "$head")" || rm -rf "$(dirname "$head")"
done
git -C "$dir" submodule update -q --init --recursive
