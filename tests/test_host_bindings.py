"""A host binding names the function the guest calls: _kept for a kept libultra/libc original, never its _recomp."""

import importlib.util
import re
import unittest
from pathlib import Path
from typing import ClassVar

HOST = Path(__file__).resolve().parents[1] / "hosts/n64recomp"

# the kept libultra/libc originals whose plain C names clash with ultra64.h (card icSo3gec4vwZ, rig-20261101T093523Z)
KEPT = (
    "lldiv", "osContGetQuery", "osContGetReadData", "osContInit", "osContReset", "osContStartQuery",
    "osContStartReadData", "osDestroyThread", "osMotorInit", "osViBlack", "osViGetCurrentFramebuffer",
    "osViSetYScale", "osViSwapBuffer", "osVirtualToPhysical", "osYieldThread",
    "__osPiRawStartDma",  # the PI adapter's, which the decomp names so
)


spec = importlib.util.spec_from_file_location("recomp_profile", HOST / "recomp_profile.py")
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)

# the kept names in the binary of job rig-20261101T131447Z that N64Recomp lists as reimplemented (symbol_lists.cpp)
KEPT_REIMPLEMENTED = (
    "osCartRomInit", "osContGetQuery", "osContGetReadData", "osContInit", "osContStartQuery", "osContStartReadData",
    "osCreatePiManager", "osDestroyThread", "osDpSetNextBuffer", "osMotorInit", "osPfsAllocateFile", "osPfsDeleteFile",
    "osPfsFileState", "osPfsFindFile", "osPfsFreeBlocks", "osPfsInitPak", "osPiGetStatus", "osPiStartDma",
    "__osSpSetPc", "osSpTaskStartGo", "osSpTaskYielded", "osSpTaskYield", "osViBlack", "osViGetCurrentFramebuffer",
    "osVirtualToPhysical", "osViSetYScale", "osViSwapBuffer",
)
# kept names no family may take: the runtime defines no <name>_recomp for them, or the host adapts them itself
KEPT_ELSEWHERE = ("__osViInit", "__osSpSetStatus", "__osSpDeviceBusy", "__osSiGetAccess", "osContReset", "lldiv",
                  "__osPiRawStartDma", "osPiRawReadIo", "osYieldThread")


def sources() -> str:
    return "\n".join(p.read_text() for p in sorted(HOST.glob("*.[ch]*")))


class HostBindingTests(unittest.TestCase):
    def test_no_wrap_binds_the_runtimes_name_of_a_kept_original(self):
        link = (HOST / "CMakeLists.txt").read_text()
        wrapped = set(re.findall(r"--wrap=(\w+)", link))
        text = sources()
        for name in KEPT:
            for dead in (name, f"{name}_recomp"):
                self.assertNotIn(dead, wrapped, f"--wrap={dead} binds a name the guest never calls")
                self.assertNotRegex(text, rf"\b__(?:wrap|real)_{dead}\b", f"{dead} is bound but never called")

    def test_every_wrap_has_its_definition_and_every_definition_its_wrap(self):
        link = (HOST / "CMakeLists.txt").read_text()
        wrapped = set(re.findall(r"--wrap=(\w+)", link))
        defined = set(re.findall(r"\b__wrap_(\w+)\s*\(", sources()))
        defined |= {name + suffix for name, suffix in re.findall(r"WRAP\((\w+),(\w+),", sources())}
        self.assertEqual(wrapped - defined, set())
        self.assertEqual(defined - wrapped, set())

    def test_the_controller_hooks_and_the_pi_and_vi_adapters_bind_the_kept_names(self):
        link = (HOST / "CMakeLists.txt").read_text()
        for bound in ("osContStartQuery_kept", "osContStartReadData_kept", "osContGetQuery_kept",
                      "__osPiRawStartDma_kept", "System_CreateVideoManager"):
            self.assertRegex(link, rf"--wrap={bound}(?!\w)")


class HostRuntimeFamilyTests(unittest.TestCase):
    families: ClassVar[list[str]] = [line.split(None, 1)[0] for line in (HOST / "HOST_RUNTIME").read_text().splitlines()
                                     if line.strip() and not line.startswith("#")]

    def test_every_family_has_its_evidence(self):
        for line in (HOST / "HOST_RUNTIME").read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                self.assertEqual(len(line.split(None, 1)), 2, f"no evidence on: {line}")

    def test_the_vi_sp_dp_and_tlb_originals_go_to_the_runtime(self):
        taken = profile.host_functions(self.families, set(KEPT_REIMPLEMENTED), set(KEPT_REIMPLEMENTED))
        names = ("osViBlack", "osViGetCurrentFramebuffer", "osViSetYScale", "osViSwapBuffer", "osSpTaskStartGo",
                 "osSpTaskYield", "osSpTaskYielded", "__osSpSetPc", "osDpSetNextBuffer", "osVirtualToPhysical")
        self.assertEqual(sorted(taken), sorted(names))

    def test_the_pi_si_and_unreimplemented_originals_stay_kept(self):
        names = set(KEPT_REIMPLEMENTED) | set(KEPT_ELSEWHERE)
        taken = profile.host_functions(self.families, names, set(KEPT_REIMPLEMENTED))
        for name in KEPT_ELSEWHERE:
            self.assertNotIn(name, taken)
        for name in ("osPiStartDma", "osPiGetStatus", "osContInit", "osContStartReadData", "osCreatePiManager"):
            self.assertNotIn(name, taken)

    def test_a_family_matching_nothing_stops_the_build(self):
        with self.assertRaises(SystemExit):
            profile.host_functions([r"osVi\w*"], {"osViSwapBuffer"}, set())

    def test_the_reimplemented_block_is_read_and_stops_at_its_end(self):
        lists = 'x reimplemented_funcs {\n "osViBlack",\n // c\n "osSpTaskYield",\n};\n ignored_funcs {\n "a",\n};'
        self.assertEqual(profile.reimplemented(lists), {"osViBlack", "osSpTaskYield"})


if __name__ == "__main__":
    unittest.main()
