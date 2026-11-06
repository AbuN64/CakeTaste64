#!/usr/bin/env python3
"""kept_audit.py FUNCS_DIR: refuse a build whose generated code keeps a libultra original that reads guest hardware.

A <name>_kept function is the original code of a libultra function the profile did not give to the runtime. Such a function that loads
a KSEG1 hardware-register address (lui 0xA4xx: MI, VI, AI, PI, RI, SI) reads it through the rdram base, which the host does not map, and
segfaults at the first call (osSetIntMask_kept on MI_INTR_MASK 0xA430000C). The functions of a HOST_KEPT family are exempt (the host's
adapters wrap them) and so are the callees of a HOST_REPLACED function. Exit 0 when none reads, else 1 with each function and its address page."""

import re
import sys
from pathlib import Path

_FUNC = re.compile(r"^RECOMP_FUNC void (\w+)\(", re.M)
_CALL = re.compile(r"\b(\w+_kept)\(rdram, ctx\)")
_KSEG1 = re.compile(r"S32\(0X(A4[0-9A-F]{2}) << 16\)")


def kseg1_readers(texts: list[str], families: list[str], replaced: frozenset[str] = frozenset()) -> dict[str, list[str]]:
    """Each _kept function the generated code references that loads a 0xA4xx0000 address, with its pages. A function is referenced when a
    function that is not itself kept (and not host-replaced, HOST_REPLACED) calls it, directly or through other kept functions; the HOST_KEPT
    families are left out."""
    bodies: dict[str, str] = {}
    for text in texts:
        heads = [(m.start(), m.group(1)) for m in _FUNC.finditer(text)]
        for (start, name), end in zip(heads, [s for s, _ in heads[1:]] + [len(text)], strict=False):
            bodies[name] = text[start:end]
    missing = sorted(replaced - bodies.keys())
    if missing:
        sys.exit(f"kept_audit: HOST_REPLACED names {' '.join(missing)}, which the generated code lacks")
    pending = [call for name, body in bodies.items() if not name.endswith("_kept") and name not in replaced for call in _CALL.findall(body)]
    reached: set[str] = set()
    while pending:
        name = pending.pop()
        if name not in reached:
            reached.add(name)
            pending += _CALL.findall(bodies.get(name, ""))
    found: dict[str, list[str]] = {}
    for name in sorted(reached):
        pages = sorted(set(_KSEG1.findall(bodies.get(name, ""))))
        if pages and not any(re.fullmatch(family, name[:-5]) for family in families):
            found[name[:-5]] = [f"0x{page}0000" for page in pages]
    return found


def main(funcs: str) -> None:
    host = Path(__file__).parent
    families = [line.split(None, 1)[0] for line in (host / "HOST_KEPT").read_text().splitlines() if line.strip() and not line.startswith("#")]
    replaced = frozenset(line.split(None, 1)[0] for line in (host / "HOST_REPLACED").read_text().splitlines() if line.strip() and not line.startswith("#"))
    sources = sorted(Path(funcs).glob("*.c"))
    if not sources:
        sys.exit(f"kept_audit: {funcs} holds no generated source")
    found = kseg1_readers([p.read_text() for p in sources], families, replaced)
    for name, pages in sorted(found.items()):
        print(f"kept_audit: {name}_kept loads KSEG1 hardware address page {' '.join(pages)}: give {name} to the runtime", file=sys.stderr)
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main(*sys.argv[1:])
