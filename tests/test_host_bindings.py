"""A host binding names the function the guest calls: _kept for a kept libultra/libc original, never its _recomp."""

import importlib.util
import json
import re
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / "hosts/n64recomp"

# the kept libultra/libc originals whose plain C names clash with ultra64.h (card icSo3gec4vwZ, rig-20261101T093523Z)
KEPT = (
    "lldiv",
    "osContGetQuery",
    "osContGetReadData",
    "osContInit",
    "osContReset",
    "osContStartQuery",
    "osContStartReadData",
    "osDestroyThread",
    "osMotorInit",
    "osViBlack",
    "osViGetCurrentFramebuffer",
    "osViSetYScale",
    "osViSwapBuffer",
    "osYieldThread",  # osVirtualToPhysical is the runtime's: the host wraps its _recomp (tlb_adapter.c)
    "__osPiRawStartDma",  # the PI adapter's, which the decomp names so
)


spec = importlib.util.spec_from_file_location("recomp_profile", HOST / "recomp_profile.py")
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


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
        for bound in (
            "osContStartQuery_kept",
            "osContStartReadData_kept",
            "osContGetQuery_kept",
            "__osPiRawStartDma_kept",
            "System_CreateVideoManager",
        ):
            self.assertRegex(link, rf"--wrap={bound}(?!\w)")


CUT = (HOST.parent.parent / "tests/fixtures/symbol_lists_cut.cpp").read_text()
KEPT_ROWS = profile.host_kept((HOST / "HOST_KEPT").read_text().splitlines())
# the decomp's names of the cut, and the _recomp definitions the runtime and the host give them
DECOMP = {
    "osInitialize",
    "osCreateThread",
    "osGetThreadId",
    "osSetIntMask",
    "__osDisableInt",
    "osContInit",
    "osContStartReadData",
    "osPiStartDma",
    "osMapTLB",
    "__osSiDeviceBusy",
    "__osDispatchThread",
    "__osPiRawStartDma",
    "lldiv",
}
DEFINED = profile.defined(
    [
        'extern "C" void osInitialize_recomp(uint8_t* rdram, recomp_context* ctx) {\n}\n'
        'extern "C" void osCreateThread_recomp(uint8_t* rdram, recomp_context* ctx) {\n}\n'
        'extern "C" void osGetThreadId_recomp(uint8_t* rdram, recomp_context* ctx) {\n}\n'
        'extern "C" void osSetIntMask_recomp(uint8_t* rdram, recomp_context* ctx) {\n}\n'
        'extern "C" void __osDisableInt_recomp(uint8_t* rdram, recomp_context* ctx) {\n}\n'
        'extern "C" void osContInit_recomp(uint8_t* rdram, recomp_context* ctx) {\n}\n'
        'extern "C" void osPiStartDma_recomp(uint8_t* rdram, recomp_context* ctx) {\n}\n'
        "void osMapTLB_recomp(uint8_t *rdram, recomp_context *ctx) {\n}\n"
        "void __osSiDeviceBusy_recomp(uint8_t*, recomp_context *ctx);\n"  # a declaration defines nothing
        "void osContStartReadData_recomp(uint8_t* rdram, recomp_context* ctx) { }\n"
    ]
)


class DerivedRuntimeTests(unittest.TestCase):
    """The profile names no runtime function: they are derived from the pinned N64Recomp symbol_lists.cpp
    (regression slice of the
    boot-idle segfault in osSetIntMask_kept, which the hand-kept list had missed after osInitialize and the
    thread functions)."""

    def derived(self) -> set[str]:
        present = (
            DECOMP & profile.listed(CUT) & DEFINED
        )  # the cut holds no osMotor or osPfs name: those families are not asked of it
        return profile.runtime_functions(
            DECOMP, profile.listed(CUT), DEFINED, [r for r in KEPT_ROWS if any(re.fullmatch(r[0], n) for n in present)]
        )

    def test_osSetIntMask_and_osInitialize_are_runtime(self):
        self.assertLessEqual(
            {"osSetIntMask", "osInitialize", "osCreateThread", "osGetThreadId", "__osDisableInt", "osMapTLB"},
            self.derived(),
        )

    def test_the_host_kept_families_stay_kept_and_so_does_a_name_nothing_defines(self):
        derived = self.derived()
        for kept in ("osContInit", "osContStartReadData", "osPiStartDma", "__osPiRawStartDma", "lldiv"):
            self.assertNotIn(kept, derived)
        self.assertNotIn(
            "__osDispatchThread", derived
        )  # listed by N64Recomp, defined by no source: kept, and fails fast if reached
        self.assertNotIn("__osSiDeviceBusy", derived)  # only declared

    def test_the_profile_keeps_no_name_of_its_own(self):
        self.assertNotIn("symbol_names", json.loads((HOST / "ragewars.profile.json").read_text())["recomp"])

    def test_a_host_kept_family_matching_nothing_stops_the_build(self):
        with self.assertRaises(SystemExit):
            profile.runtime_functions(DECOMP, profile.listed(CUT), DEFINED, [(r"osNoSuch\w*", "x")])

    def test_a_host_kept_row_without_its_reason_stops_the_build(self):
        with self.assertRaises(SystemExit):
            profile.host_kept(["osContInit"])

    def test_the_lists_are_read_across_both_blocks_and_stop_at_their_end(self):
        lists = profile.listed(CUT)
        self.assertLessEqual({"osSetIntMask", "osMapTLB", "__osDispatchThread"}, lists)
        self.assertNotIn("lldiv", lists)  # renamed_funcs is not a runtime list

    def test_no_function_the_host_wraps_as_recomp_is_a_name_the_host_keeps(self):
        wrapped = set(re.findall(r"--wrap=(\w+)_recomp\b", (HOST / "CMakeLists.txt").read_text()))
        for name in wrapped:
            self.assertFalse(
                any(re.fullmatch(family, name) for family, _ in KEPT_ROWS),
                f"{name}_recomp is wrapped but HOST_KEPT keeps {name}",
            )


class KeptAuditTests(unittest.TestCase):
    """No _kept function the generated code references may load a KSEG1 hardware address (0xA4xxxxxx): the host
    maps no registers there."""

    GEN = (
        "RECOMP_FUNC void osSetIntMask_kept(uint8_t* rdram, recomp_context* ctx) {\n    ctx->r10 = S32(0XA430 << 16);\n}\n"  # noqa: E501
        "RECOMP_FUNC void osContInit_kept(uint8_t* rdram, recomp_context* ctx) {\n    ctx->r10 = S32(0XA480 << 16);\n}\n"  # noqa: E501
        "RECOMP_FUNC void helper_kept(uint8_t* rdram, recomp_context* ctx) {\n    ctx->r10 = S32(0XA430 << 16);\n}\n"
        "RECOMP_FUNC void osCreateThread_kept(uint8_t* rdram, recomp_context* ctx) {\n    ctx->r8 = S32(0X800E << 16);\n}\n"  # noqa: E501
        "RECOMP_FUNC void Game_Update(uint8_t* rdram, recomp_context* ctx) {\n    osSetIntMask_kept(rdram, ctx);\n"
        "    osCreateThread_kept(rdram, ctx);\n    osContInit_kept(rdram, ctx);\n}\n"
    )

    def families(self) -> list[str]:
        return [family for family, _ in KEPT_ROWS]

    def test_a_referenced_kept_function_that_reads_mi_is_named(self):
        audit = importlib.util.spec_from_file_location("kept_audit", HOST / "kept_audit.py")
        module = importlib.util.module_from_spec(audit)
        audit.loader.exec_module(module)
        found = module.kseg1_readers([self.GEN], self.families())
        self.assertEqual(
            found, {"osSetIntMask": ["0xA4300000"]}
        )  # osContInit is the host's, helper is never referenced, a 0x800E page is RDRAM

    def test_the_callees_of_a_host_replaced_function_are_not_referenced(self):
        audit = importlib.util.spec_from_file_location("kept_audit", HOST / "kept_audit.py")
        module = importlib.util.module_from_spec(audit)
        audit.loader.exec_module(module)
        gen = (
            self.GEN
            + "RECOMP_FUNC void Vm_Create(uint8_t* rdram, recomp_context* ctx) {\n    helper_kept(rdram, ctx);\n}\n"
        )
        self.assertEqual(sorted(module.kseg1_readers([gen], self.families())), ["helper", "osSetIntMask"])
        self.assertEqual(
            sorted(module.kseg1_readers([gen], self.families(), frozenset({"Vm_Create"}))), ["osSetIntMask"]
        )
        with self.assertRaises(SystemExit):
            module.kseg1_readers([gen], self.families(), frozenset({"Nothing"}))


if __name__ == "__main__":
    unittest.main()
