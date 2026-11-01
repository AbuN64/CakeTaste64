#!/usr/bin/env python3
"""recomp_profile.py LIVE_PROFILE BUILD: the taste profile LIVE_PROFILE with every recomp path moved into BUILD.

The decomp is BUILD/decomp, the generated output goes to BUILD/recomp, and N64Recomp and RSPRecomp are the ones built in
BUILD/deps/N64Recomp/build. Everything else (ROMs, sourced facts, the runtime-provided function list) is the live
profile's, except that a symbol_names entry whose address the decomp at the pin names differently is dropped and listed
on stderr: the decomp's name decides, so that function stays recompiled from its original code and is never swapped for
the runtime's. The functions HOST_RUNTIME lists are the exception the host declares: each is added at the address the
decomp gives its name, and one the decomp does not name stops the build. Prints the derived profile JSON."""

import json
import re
import sys
from pathlib import Path

live, build = sys.argv[1:]
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
    missing = [name for name, _ in host_runtime if name not in by_name]
    if missing:
        sys.exit(f"HOST_RUNTIME names the decomp at the pin lacks for {version}: {', '.join(missing)}")
    for name, source in host_runtime:
        runtime[f"0x{by_name[name]:08X}"] = {"name": name, "source": f"hosts/n64recomp/HOST_RUNTIME: {source}"}
print(json.dumps(profile, indent=1))
