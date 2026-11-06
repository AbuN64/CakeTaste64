#!/usr/bin/env python3
"""recomp_profile.py LIVE_PROFILE BUILD VERSION ROM: the taste profile LIVE_PROFILE (a tracked file, hosts/n64recomp/ragewars.profile.json
for RageWars) with every recomp path moved into BUILD.

The decomp is BUILD/decomp, the generated output goes to BUILD/recomp, and N64Recomp and RSPRecomp are the ones built in
BUILD/deps/N64Recomp/build. ROM is the dump of VERSION, the path ci/oracle found in the decomp working copy: it replaces the live profile's
entry for VERSION, since a relative path of the live file resolves in whatever worktree holds it. Everything else (the other ROMs, sourced facts) is the
live profile's. The runtime functions are never kept by name: they are derived, every function the decomp names that N64Recomp
lists (symbol_lists.cpp reimplemented_funcs and ignored_funcs at the pinned N64Recomp), that a runtime or host source defines a
<name>_recomp for, and that no HOST_KEPT family keeps for the host's own adapters, at the address the decomp gives its name; a family
matching none stops the build. Prints the derived profile JSON."""

import json
import os
import re
import sys
from pathlib import Path


def listed(symbol_lists: str) -> set[str]:
    """The names in N64Recomp's reimplemented_funcs and ignored_funcs blocks: the built-in libultra lists the patched N64Recomp keeps
    as <name>_kept unless the config gives them to the runtime."""
    names: set[str] = set()
    for block in ("reimplemented_funcs", "ignored_funcs"):
        names |= set(re.findall(r'"(\w+)"', symbol_lists.split(block, 1)[1].split("};", 1)[0]))
    return names


def defined(texts: list[str]) -> set[str]:
    """The names some source defines a <name>_recomp for (a definition, not a declaration or a call)."""
    return {name for text in texts for name in re.findall(r"\bvoid\s+(\w+)_recomp\s*\([^;{]*\)\s*\{", text)}


def host_kept(lines: list[str]) -> list[tuple[str, str]]:
    """HOST_KEPT's (family, reason) rows."""
    rows = [line.split(None, 1) for line in lines if line.strip() and not line.startswith("#")]
    for row in rows:
        if len(row) != 2:
            sys.exit(f"HOST_KEPT row without a reason: {row[0]}")
    return [(family, reason) for family, reason in rows]


def runtime_functions(decomp_names: set[str], lists: set[str], definitions: set[str], kept: list[tuple[str, str]]) -> set[str]:
    """Each decomp function N64Recomp lists, some source defines a <name>_recomp for, and no HOST_KEPT family keeps."""
    for family, _ in kept:
        if not any(re.fullmatch(family, n) for n in decomp_names & lists & definitions):
            sys.exit(f"HOST_KEPT family {family} matches no function the decomp names, N64Recomp lists and a source defines")
    return {n for n in decomp_names & lists & definitions if not any(re.fullmatch(f, n) for f, _ in kept)}


def beside(live: str, value: str) -> str:
    """A path of the live profile, absolute: a relative one resolves against the profile file, as Profile does."""
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(live)), value))


def main(live: str, build: str, version: str, rom: str) -> None:
    profile = json.loads(Path(live).read_text())
    profile.update(project_root=build + "/decomp", runtime_root=build + "/taste-run", output_root=build + "/taste-run")
    recomp = profile["recomp"]
    recomp["decomp_root"] = build + "/decomp"
    for key in ("roms", "function_sections"):  # version to path objects; a missing key is refused by the recomp section
        if key in recomp:
            recomp[key] = {version: beside(live, path) for version, path in recomp[key].items()}
    if "input_bindings" in recomp:  # the tracked bindings file stays beside the live profile, not in BUILD
        recomp["input_bindings"] = beside(live, recomp["input_bindings"])
    recomp.setdefault("roms", {})[version] = rom
    recomp["gen_root"] = build + "/recomp"
    recomp["tools"] = {
        "n64recomp": build + "/deps/N64Recomp/build/N64Recomp",
        "rsprecomp": build + "/deps/N64Recomp/build/RSPRecomp",
    }
    host = Path(__file__).parent
    kept = host_kept((host / "HOST_KEPT").read_text().splitlines())
    lists = listed(Path(build, "deps/N64Recomp/src/symbol_lists.cpp").read_text())
    sources = [p.read_text(errors="replace") for root, pattern in ((Path(build, "deps/N64ModernRuntime"), "*.cpp"), (host, "*.c*"))
               for p in sorted(root.rglob(pattern)) if p.name != "libultra_gaps.c"]  # the gaps file defines the unimplemented stubs
    definitions = defined(sources)
    symbols = build + "/decomp/" + recomp["symbols_file"]
    recomp["symbol_names"] = {}
    for version in recomp["roms"]:
        # the decomp's symbol list at the pin: `name = 0xADDRESS; // type:func`; a typed function wins
        names: dict[int, list[tuple[str, bool]]] = {}
        for line in Path(symbols.format(version=version)).read_text().splitlines():
            match = re.match(r"\s*(\S+)\s*=\s*(0x[0-9A-Fa-f]+)\s*;(.*)", line)
            if match:
                names.setdefault(int(match.group(2), 0), []).append((match.group(1), "type:func" in match.group(3)))
        by_name = {n: a for a, found in names.items() for n, _ in found}
        recomp["symbol_names"][version] = {
            f"0x{by_name[name]:08X}": {"name": name, "source": "derived from N64Recomp symbol_lists.cpp by recomp_profile.py"}
            for name in sorted(runtime_functions(set(by_name), lists, definitions, kept))}
    print(json.dumps(profile, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
