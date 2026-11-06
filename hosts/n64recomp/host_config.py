#!/usr/bin/env python3
"""host_config.py OUT STATE [--abumas PATH]: this tree's own unbake host config, written to OUT from unbake's template.

The operator's ~/.config/unbake/unbake.toml is never read or edited. Every value comes from this host or the tree:
tools on PATH (git, make, cpp, the mips binutils, splat; armips only when found), abumas as given (ci/oracle builds it),
the cache and toolchain folders inside STATE (the tree's git-ignored state), cores and workers "auto", and the memory
limits from what the host has (/proc/meminfo), never a literal. Run with the python of the venv that holds unbake."""

import argparse
import os
import re
import shutil
import sys
from importlib import resources
from pathlib import Path
from string import Template

REQUIRED = {"git": "git", "make": "make", "cpp": "cpp", "mips_as": "mips-linux-gnu-as", "mips_ld": "mips-linux-gnu-ld",
            "mips_objcopy": "mips-linux-gnu-objcopy", "mips_objdump": "mips-linux-gnu-objdump", "splat": "splat"}
OPTIONAL = {"armips": "armips"}  # a tool the template lists as optional: its line is dropped when it is not found


def memory_total(meminfo: str) -> int:
    """MemTotal of /proc/meminfo text, in bytes."""
    found = re.search(r"^MemTotal:\s+(\d+) kB$", meminfo, re.MULTILINE)
    if not found:
        raise SystemExit("host_config: /proc/meminfo has no MemTotal")
    return int(found[1]) * 1024


def render(template: str, state: Path, abumas: str | None, cpus: int, memory: int, disk_free: int,
           which=shutil.which) -> str:
    """The host config for STATE from the unbake host template; a missing required tool stops it, naming the tool."""
    tools, missing = {}, []
    for key, binary in REQUIRED.items():
        found = which(binary)
        tools[key] = found
        if not found:
            missing.append(binary)
    if missing:
        raise SystemExit(f"host_config: not on PATH: {', '.join(missing)}")
    for key, binary in OPTIONAL.items():
        tools[key] = which(binary)
    tools["abumas"] = abumas
    values = {
        "cores": '"auto"', "workers": '"auto"',
        "memory_parent_bytes": memory // 16, "memory_worker_bytes": memory // (2 * cpus),
        "cache_dir": state / "unbake-cache", "cache_max_bytes": disk_free // 4,
        "toolchain_root": state / "toolchains",
        "serial_seconds": 2, "serial_cores": 2, "pool_fill": 0.8, "pool_fanout": 2,
        "author_name": "CakeTaste oracle", "author_email": "oracle@mo.internal",
        **{key: path for key, path in tools.items() if path},
    }
    lines = []
    for line in template.splitlines():
        key = re.match(r"(\w+) = \"\$\{(\w+)\}\"$", line)
        if key and key[1] in tools and not tools[key[1]]:
            continue
        lines.append(line)
    return Template("\n".join(lines) + "\n").substitute(values)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--abumas")
    args = parser.parse_args(argv)
    template = (resources.files("unbake.resources") / "templates" / "host.toml").read_text()
    args.state.mkdir(parents=True, exist_ok=True)
    text = render(template, args.state.resolve(), args.abumas, os.process_cpu_count(),
                  memory_total(Path("/proc/meminfo").read_text()), shutil.disk_usage(args.state).free)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
