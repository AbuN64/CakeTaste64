"""Oracle facts the host needs beyond the recompile, each read from a tracked source of the decomp or the repository.

One small reader per fact family. A fact with no tracked source is not invented here: the reader returns only what its
source states, and the host's profile readers refuse the missing field by name."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from cake_taste import TasteError

# include/PR/os_tlb.h: OS_PM_1M is page mask 0x01fe000; a mask covers (mask + 0x2000) / 2 bytes per page.
_ENTRY_STUB = "sdk/BOOT.s"
_R4300 = "include/PR/R4300.h"
_CONVERT = "include/PR/os_convert.h"
_SYSTEM = "include/PR/os_system.h"
_VI_INIT = "sdk/libultra/io/vi/vi.CPP"
_NTSC_VI_HZ = 60  # the NTSC field rate the host's VI timing supports (59.94 Hz nominal)


def _word(text: str, register: str) -> int:
    """The 32-bit constant the stub loads into REGISTER: `lui r, hi` then `ori r, r, lo`, or `li r, value`."""
    loads = re.findall(
        rf"(?:lui\s+{register},\s*(0x[0-9A-Fa-f]+)\s*\n\s*ori\s+{register},\s*{register},\s*(0x[0-9A-Fa-f]+)"
        rf"|lui\s+{register},\s*(0x[0-9A-Fa-f]+)\s*\n|li\s+{register},\s*(0x[0-9A-Fa-f]+))", text)
    if not loads:
        raise TasteError("recomp.facts", f"{_ENTRY_STUB} loads no constant into {register}")
    high, low, upper, plain = loads[-1]  # the last load before the osMapTLB call wins over a loop counter
    return (int(high, 16) << 16) | int(low, 16) if high else int(upper, 16) << 16 if upper else int(plain, 16)


def symbol_address(symbols: str, name: str) -> int:
    """The address the decomp's symbol list gives NAME (`name = 0xADDRESS; // ...`)."""
    match = re.search(rf"^{re.escape(name)}\s*=\s*(0x[0-9A-Fa-f]+)\s*;", symbols, re.M)
    if not match:
        raise TasteError("recomp.facts", f"the decomp's symbol list names no {name}")
    return int(match.group(1), 16)


def _define(text: str, name: str, path: str) -> str:
    match = re.search(rf"^#define\s+{name}\s+(.+?)\s*(?:/\*.*)?$", text, re.M)
    if not match:
        raise TasteError("recomp.facts", f"{path} defines no {name}")
    return match.group(1).strip()


def _number(expression: str) -> int:
    return int(re.sub(r"[uUlL]+$", "", expression), 0)


def _function_starts(symbols: str) -> list[int]:
    return sorted(int(m.group(1), 16) for m in re.finditer(r"=\s*(0x[0-9A-Fa-f]+)\s*;[^\n]*type:func", symbols))


def _boot_main(stub: str, symbols: str, version: str) -> int:
    """The continuation the entry stub jumps to for VERSION: the symbol it loads into a0 under its VERSION_* branch
    is an absolute symbol holding a physical address; its kseg0 form must be a function start of the symbol list."""
    macro = "VERSION_" + version.upper().replace("-", "_")
    branch = re.search(rf"#(?:el)?if\s+{macro}\s*\n\s*lui\s+a0,\s*%hi\((\w+)\)", stub)
    if not branch:
        raise TasteError("recomp.facts", f"{_ENTRY_STUB} loads no boot continuation under {macro}")
    main = 0x80000000 | symbol_address(symbols, branch.group(1))
    if main not in _function_starts(symbols):
        raise TasteError("recomp.facts", f"{branch.group(1)} (0x{main:08X} in kseg0) is no function start")
    return main


def boot_facts(decomp_root: Path, symbols_path: Path, version: str, rdram_size: int | None) -> dict[str, Any]:
    """Boot facts the decomp states: the initial stack and the static TLB mapping from its entry stub
    (sdk/BOOT.s: `sp`, then osMapTLB's index a0, page mask a1, virtual address a2, even physical a3, extent sw 0x10(sp),
    entry flags sw 0x14(sp)) and its continuation; the faulted-thread, clock-rate and initial VI mode addresses from
    its symbol list; the reset Status from include/PR/R4300.h. The TLB ABI is osMapTLB's (include/PR/os_tlb.h:
    the fourth and fifth arguments are EntryLo values carrying the flags). RDRAM_SIZE is the host's own choice
    (the profile's sourced constant), not a decomp read: osMemSize only receives it."""
    if rdram_size is None:
        raise TasteError("recomp.facts", "the profile names no recomp.rdram_size constant")
    stub = (decomp_root / _ENTRY_STUB).read_text()
    stub = stub.split("LEAF(Boot_EntryPoint)", 1)[-1]
    mask = _word(stub, "a1")
    page = (mask + 0x2000) // 2
    extent = _word(stub, "t1")
    entry = {"index": _word(stub, "a0"), "page_size": page, "size": extent,
             "vaddr": _word(stub, "a2"), "paddr": _word(stub, "a3")}
    if extent not in (page, page * 2):
        raise TasteError("recomp.facts", f"{_ENTRY_STUB}: TLB extent 0x{extent:X} is not one or two 0x{page:X} pages")
    symbols = symbols_path.read_text()
    # The IPL3 leaves the FPU usable in 64-bit register mode; BOOT.s never writes Status before the game's first mfc1.
    r4300 = (decomp_root / _R4300).read_text()
    status = _number(_define(r4300, "SR_CU1", _R4300)) | _number(_define(r4300, "SR_FR", _R4300))
    return {
        "main": _boot_main((decomp_root / _ENTRY_STUB).read_text(), symbols, version),
        "sp": _word(stub, "sp"),
        "rdram_size": rdram_size,
        "tlb": [entry],
        "tlb_map_abi": "entrylo_flags",
        "faulted_thread": f"0x{symbol_address(symbols, '__osFaultedThread'):08X}",
        # g_systemClockFrequency is the decomp's osClockRate: sdk/libultra/os/initialize.c scales it by 3/4 in place.
        "os_clock_rate": f"0x{symbol_address(symbols, 'g_systemClockFrequency'):08X}",
        "vi_mode": f"0x{_vi_mode(decomp_root, symbols):08X}",
        "registers": {"status": status},
    }


def _vi_mode(decomp_root: Path, symbols: str) -> int:
    """The mode __osViInit installs for the NTSC TV type (the branch of neither PAL nor MPAL): osViModeTable."""
    init = (decomp_root / _VI_INIT).read_text()
    if not re.search(r"else\s*\{\s*contexts\[1\]\.modep\s*=\s*osViModeTable;", init):
        raise TasteError("recomp.facts", f"{_VI_INIT} installs no osViModeTable for the default TV type")
    return symbol_address(symbols, "osViModeTable")


def cpu_facts(decomp_root: Path, symbols_path: Path, version: str) -> dict[str, Any]:
    """CPU timing and native-code facts: the count rate from include/PR/os_convert.h (OS_CPU_COUNTER), the VI rate
    of the NTSC TV type the decomp uses, and the native ranges from the function extents of
    versions/<v>/libultra_names.tsv (each row's start to the next function start of the symbol list, touching
    ranges merged)."""
    convert = (decomp_root / _CONVERT).read_text()
    rate = _number(_define(convert, "OS_CLOCK_RATE", _CONVERT))
    if _define(convert, "OS_CPU_COUNTER", _CONVERT).replace(" ", "") != "(OS_CLOCK_RATE*3/4)":
        raise TasteError("recomp.facts", f"{_CONVERT}: OS_CPU_COUNTER is no longer OS_CLOCK_RATE*3/4")
    system = (decomp_root / _SYSTEM).read_text()
    tv_source = (decomp_root / _VI_INIT).read_text()
    if _number(_define(system, "OS_TV_NTSC", _SYSTEM)) != 1 or "g_consoleTvType" not in tv_source:
        raise TasteError("recomp.facts", "the decomp's TV type is not the NTSC one this reader knows")
    starts = _function_starts(symbols_path.read_text())
    names = (decomp_root / f"versions/{version}/libultra_names.tsv").read_text().splitlines()[1:]
    rows = [line.split("\t") for line in names]
    ranges: list[list[int]] = []
    for vram in sorted(int(row[0], 16) for row in rows if len(row) == 3):
        after = [s for s in starts if s > vram]
        if vram not in starts or not after:
            raise TasteError("recomp.facts", f"libultra_names.tsv row 0x{vram:08X} is no function with an end")
        if ranges and ranges[-1][1] == vram:
            ranges[-1][1] = after[0]
        else:
            ranges.append([vram, after[0]])
    if not ranges:
        raise TasteError("recomp.facts", "libultra_names.tsv names no function")
    return {"timing": {"count_hz": rate * 3 // 4, "vi_hz": _NTSC_VI_HZ}, "native_code": ranges}


def input_facts(bindings: Path | None) -> dict[str, Any]:
    """The tracked controller bindings (the `input` object of the profile's input_bindings file)."""
    if bindings is None:
        raise TasteError("recomp.facts", "the profile names no recomp.input_bindings file")
    return json.loads(bindings.read_text())["input"]
