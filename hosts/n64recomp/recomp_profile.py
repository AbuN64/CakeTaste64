#!/usr/bin/env python3
"""recomp_profile.py LIVE_PROFILE BUILD: the taste profile LIVE_PROFILE with every recomp path moved into BUILD.

The decomp is BUILD/decomp, the generated output goes to BUILD/recomp, and N64Recomp and RSPRecomp are the ones built in
BUILD/deps/N64Recomp/build. Everything else (ROMs, sourced facts, the runtime-provided function list) is the live
profile's, except that a symbol_names entry whose address the decomp at the pin names differently is dropped and listed
on stderr: the decomp's name decides, so that function stays recompiled from its original code and is never swapped for
the runtime's. The families HOST_RUNTIME lists are the exception the host declares: every function the decomp names that
matches a family and that N64Recomp lists as reimplemented (the runtime defines its <name>_recomp) is added at the
address the decomp gives its name, and a family matching none stops the build. Prints the derived profile JSON."""

import json
import re
import sys
from pathlib import Path


def reimplemented(symbol_lists: str) -> set[str]:
    """The names in N64Recomp's reimplemented_funcs block: the ones the runtime defines a <name>_recomp for."""
    block = symbol_lists.split("reimplemented_funcs", 1)[1].split("};", 1)[0]
    return set(re.findall(r'"(\w+)"', block))


def host_functions(families: list[str], decomp_names: set[str], runtime_defines: set[str]) -> dict[str, str]:
    """Each decomp function a HOST_RUNTIME family matches and the runtime defines, with the family that matched it."""
    found: dict[str, str] = {}
    for family in families:
        matched = {n for n in decomp_names & runtime_defines if re.fullmatch(family, n)}
        if not matched:
            sys.exit(f"HOST_RUNTIME family {family} matches no reimplemented function the decomp names")
        found.update({name: family for name in matched})
    return found


def main(live: str, build: str) -> None:
    profile = json.loads(Path(live).read_text())
    profile.update(project_root=build + "/decomp", runtime_root=build + "/taste-run", output_root=build + "/taste-run")
    recomp = profile["recomp"]
    recomp["decomp_root"] = build + "/decomp"
    recomp["gen_root"] = build + "/recomp"
    recomp["tools"] = {
        "n64recomp": build + "/deps/N64Recomp/build/N64Recomp",
        "rsprecomp": build + "/deps/N64Recomp/build/RSPRecomp",
    }
    host_runtime = [line.split(None, 1) for line in (Path(__file__).parent / "HOST_RUNTIME").read_text().splitlines()
                    if line.strip() and not line.startswith("#")]
    runtime_defines = reimplemented(Path(build, "deps/N64Recomp/src/symbol_lists.cpp").read_text())
    symbols = build + "/decomp/" + recomp["symbols_file"]
    for version, runtime in recomp.get("symbol_names", {}).items():
        # the decomp's symbol list at the pin: `name = 0xADDRESS; // type:func`; a typed function wins
        names = {}
        for line in Path(symbols.format(version=version)).read_text().splitlines():
            match = re.match(r"\s*(\S+)\s*=\s*(0x[0-9A-Fa-f]+)\s*;(.*)", line)
            if match:
                names.setdefault(int(match.group(2), 0), []).append((match.group(1), "type:func" in match.group(3)))
        for address in sorted(runtime, key=lambda a: int(a, 0)):
            have = sorted(names.get(int(address, 0), []), key=lambda n: not n[1])
            entry = runtime[address]
            wanted = entry["name"] if isinstance(entry, dict) else entry
            if have and have[0][0] != wanted:
                print(f"runtime name dropped: {address} {wanted} (the decomp names it {have[0][0]})", file=sys.stderr)
                del runtime[address]
        by_name = {n: a for a, found in names.items() for n, _ in found}
        evidence = dict(host_runtime)
        for name, family in host_functions(list(evidence), set(by_name), runtime_defines).items():
            source = f"hosts/n64recomp/HOST_RUNTIME: {evidence[family]}"
            runtime[f"0x{by_name[name]:08X}"] = {"name": name, "source": source}
    print(json.dumps(profile, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
