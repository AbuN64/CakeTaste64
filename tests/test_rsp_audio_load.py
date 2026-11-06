"""The audio microcode's first call lands on its command-list loader only at the IMEM address the boot microcode
really loads it to.

Regression slice of the boot-idle exit 3 (RspExitReason 3, the dispatcher read a float as a command): the profile
put the text at
0x04001000, so `jal 0x1150` (a 12-bit IMEM target) hit a DMA-write tail and the first command list was never loaded
into DMEM.
The load address is derived from the boot microcode's code (segment rsp_boot, ROM 0xDD840 in us-rev1), never typed
in the profile."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from test_recomp import build

from cake_taste import TasteError
from cake_taste.recomp.generate import _boot_load_address, load_layout
from cake_taste.recomp.settings import load

PROFILE = Path(__file__).resolve().parents[1] / "hosts/n64recomp/ragewars.profile.json"
# the boot microcode words at the offsets that matter (us-rev1 ROM 0xDD840): the load address register, then the jump
# through it
BOOT = {0x10: 0x20071080,  # addi a3, zero, 0x1080   the IMEM address the ucode text is DMAed to
        0x34: 0x00E00008}  # jr a3
# words of the rsp_audio_code segment (ROM 0xDFE30): the first call at text offset 0x38 and the words at the offsets
# it can reach
TEXT = {0x38: 0x0D000454,  # jal 0x1150
        0xD0: 0x23E50000,  # addi a1, ra, 0   the list loader: saves ra,
        0xD4: 0x001C1020,  # add v0, zero, gp    then DMAs from the list pointer
        0x150: 0x40831800}  # mtc0 v1, SP_WR_LEN   the tail of a DMA write


def settings_with_boot(root: Path, words: dict[int, int]):
    settings = replace(load(build(root)), rsp_boot_segment="rsp_code")
    rom = bytearray((root / "rom.z64").read_bytes())
    for offset, word in words.items():
        rom[0x4000 + offset:0x4000 + offset + 4] = word.to_bytes(4, "big")
    (root / "rom.z64").write_bytes(rom)
    return settings


class RspAudioLoadTests(unittest.TestCase):
    def test_the_profile_derives_the_load_address_from_the_boot_ucode(self):
        rsp = json.loads(PROFILE.read_text())["recomp"]["rsp_audio"]
        self.assertEqual(rsp["boot_segment"], "rsp_boot")
        self.assertNotIn("execution_vram", rsp)

    def test_the_load_address_is_the_imem_constant_the_boot_ucode_jumps_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = settings_with_boot(root, BOOT)
            execution = _boot_load_address(settings, "v1", load_layout(settings, "v1"))
        self.assertEqual(execution, 0x04001080)
        target = ((TEXT[0x38] & 0x03FFFFFF) << 2) & 0xFFF  # RSP jumps keep 12 bits of IMEM
        offset = target - (execution & 0xFFF)
        self.assertEqual(TEXT[offset], 0x23E50000)
        self.assertEqual(TEXT[offset + 4], 0x001C1020)
        self.assertEqual(TEXT[target], 0x40831800)  # at the 0x04001000 base the same call reaches a DMA-write tail

    def test_a_boot_ucode_with_no_jump_through_an_imem_constant_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = settings_with_boot(root, {0x10: 0x20070040, 0x34: 0x00E00008})
            with self.assertRaises(TasteError):
                _boot_load_address(settings, "v1", load_layout(settings, "v1"))
