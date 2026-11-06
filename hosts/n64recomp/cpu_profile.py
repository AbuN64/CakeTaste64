"""Render the logical guest clock's VI rate and the charged-function table (RUNTIME.md "Virtual guest time")."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def render(facts: dict) -> str:
    """The VI rate the host measures its guest clock against (RUNTIME.md "Virtual guest time"): the charge itself is
    measured by the host at its own start, never a fact."""
    vi_hz = facts["cpu"]["timing"]["vi_hz"]
    if type(vi_hz) is not int or vi_hz <= 0:
        raise ValueError("cpu.timing.vi_hz must be a positive integer")
    if vi_hz != 60:
        raise ValueError("the pinned VI runtime currently supports only 60 Hz")
    return f"#pragma once\n#define HOST_VI_HZ {vi_hz}\n"


SYMBOL = re.compile(r'\{ name = "([A-Za-z_][A-Za-z0-9_]*)", vram = 0x([0-9A-Fa-f]+),')
UNNAMED = re.compile(r"(?:func|asm|entry)_[0-9A-Fa-f]{8}")


def render_native(facts: dict, symbols: str) -> str:
    """Which recompiled functions charge guest work under the entries clock (RUNTIME.md "Virtual guest time"):
    every function of symbols.toml whose vram lies in no cpu.native_code range [lo, hi), the code the port runs
    natively (the SDK range and the functions it binds to native code). Every row carries the decomp's name: a
    function the decomp leaves unnamed (func_, asm_ or entry_ plus its address) stops the render with its address
    listed, never a row under the placeholder."""
    ranges = facts["cpu"]["native_code"]
    if not isinstance(ranges, list) or not ranges or not all(
            isinstance(r, list) and len(r) == 2 and all(type(v) is int for v in r) and r[0] < r[1] for r in ranges):
        raise ValueError("cpu.native_code must be a non-empty list of [lo, hi) vram ranges")
    functions = {}
    for name, vram in SYMBOL.findall(symbols):
        if functions.setdefault(name, int(vram, 16)) != int(vram, 16):
            raise ValueError(f"symbols.toml names {name} at two addresses")
    if not functions:
        raise ValueError("symbols.toml lists no functions")
    unnamed = sorted({vram for name, vram in functions.items() if UNNAMED.match(name)})
    if unnamed:
        raise ValueError(f"the decomp names no function at {len(unnamed)} addresses: "
                         + " ".join(f"0x{vram:08X}" for vram in unnamed))
    rows = [f'    {{"{name}", {int(not any(lo <= vram < hi for lo, hi in ranges))}}},'
            for name, vram in sorted(functions.items(), key=lambda item: item[0].encode())]
    return ("#pragma once\n// Sorted by name (strcmp); charged = an entry is guest work.\n"
            "static const struct { const char *name; unsigned char charged; } host_functions[] = {\n"
            + "\n".join(rows) + "\n};\n")


if __name__ == "__main__":
    facts = json.loads(Path(sys.argv[1]).read_text())
    Path(sys.argv[2]).write_text(render(facts))
    Path(sys.argv[4]).write_text(render_native(facts, Path(sys.argv[3]).read_text()))
