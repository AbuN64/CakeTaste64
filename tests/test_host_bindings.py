"""A host binding names the function the guest calls: _kept for a kept libultra/libc original, never its _recomp."""

import re
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / "hosts/n64recomp"

# the kept libultra/libc originals whose plain C names clash with ultra64.h (card icSo3gec4vwZ, rig-20261101T093523Z)
KEPT = (
    "lldiv", "osContGetQuery", "osContGetReadData", "osContInit", "osContReset", "osContStartQuery",
    "osContStartReadData", "osDestroyThread", "osMotorInit", "osViBlack", "osViGetCurrentFramebuffer",
    "osViSetYScale", "osViSwapBuffer", "osVirtualToPhysical", "osYieldThread",
    "__osPiRawStartDma",  # the PI adapter's, which the decomp names so
)


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


if __name__ == "__main__":
    unittest.main()
