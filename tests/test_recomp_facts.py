import ast
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cake_taste import TasteError
from cake_taste.recomp import generate
from cake_taste.recomp.facts import boot_facts, cpu_facts, input_facts, symbol_address

HOSTS = Path(__file__).parent.parent / "hosts/n64recomp"

# Real slices of the decomp (RageWarsDecomp): the file each sits in, quoted as it reads there.
STUB = """#include "PR/R4300.h"
LEAF(Boot_EntryPoint)
    lui     sp, 0x803F
    ori     sp, sp, 0xFFC0
    li      a0, 0x1E
1:
    nop
    li      a0, 0x1F
    lui     a1, 0x1F
    ori     a1, a1, 0xE000
    lui     a2, 0x20
    li      a3, 0x0
    lui     t1, 0x10
    sw      t1, 0x10(sp)
    li      t0, 0x7
    jal     rw_boot_entry+0x88
    sw      t0, 0x14(sp)
#if VERSION_EU
    lui     a0, %hi(Sched_RestoreBootMemoryAddressEu)
    addiu   a0, a0, %lo(Sched_RestoreBootMemoryAddressEu)
#elif VERSION_US
    lui     a0, %hi(ItemMenu_RestoreBootMemory_Address)
    addiu   a0, a0, %lo(ItemMenu_RestoreBootMemory_Address)
#elif VERSION_US_REV1
    lui     a0, %hi(Sched_RestoreBootMemoryAddressUsRev1)
    addiu   a0, a0, %lo(Sched_RestoreBootMemoryAddressUsRev1)
#else
    lui     a0, %hi(Sched_RestoreBootMemoryAddressDe)
#endif
    jalr    a0
"""
SYMBOLS = """osMemSize = 0x80000318; //
g_consoleTvType = 0x80000300; //
__osRunningThread = 0x800D92A0; // rom:0xD9EA0
__osFaultedThread = 0x800D92A4; // rom:0xD9EA4
osViModeTable = 0x800D8470; // rom:0xD9070
g_systemClockFrequency = 0x8014FD90; //
Sched_RestoreBootMemoryAddressUsRev1 = 0x002A81B0; //
osMapTLB = 0x802B2440; // type:func rom:0xB3040
ItemMenu_ToggleEntry = 0x802A8170; // type:func rom:0xA8D70
ItemMenu_RestoreBootMemory = 0x802A81B0; // type:func rom:0xA8DB0
ItemMenu_RestoreBootMemoryNoOp = 0x802A853C; // type:func rom:0xA913C
osScRemoveClient = 0x8028F89C; // type:func rom:0x9049C
Sched_GetCommandQueue = 0x8028F92C; // type:func rom:0x9052C
guLookAt = 0x802BB740; // type:func rom:0xBC340
guLookAtF = 0x802BB9D0; // type:func rom:0xBC5D0
guMtxIdent = 0x802BBC50; // type:func rom:0xBC850
"""
R4300 = """#define	SR_CU1		0x20000000	/* Coprocessor 1 usable */
#define	SR_FR		0x04000000	/* MIPS III FP register mode */
"""
CONVERT = """#define	OS_CLOCK_RATE		62500000LL
#define	OS_CPU_COUNTER		(OS_CLOCK_RATE*3/4)
"""
SYSTEM = """#define	OS_TV_PAL		0
#define	OS_TV_NTSC		1
#define	OS_TV_MPAL		2
"""
VI_INIT = """    tv_type = g_consoleTvType;
    if (tv_type == 0) {
        contexts[1].modep = &g_videoInterfaceModeDefault;
    } else if (tv_type == 2) {
        contexts[1].modep = &g_videoInterfaceModeAlternate;
    } else {
        contexts[1].modep = osViModeTable;
    }
"""
NAMES = ("vram\tname\tbasis\n"
         "0x8028F89C\tosScRemoveClient\tmachine code\n"
         "0x802BB740\tguLookAt\tlibreultra 1aca5c13 src/gu/lookat.c; reviewed source correspondence\n"
         "0x802BB9D0\tguLookAtF\tlibreultra 1aca5c13 src/gu/lookat.c; reviewed source correspondence\n")
INPUT = HOSTS / "ragewars.input.json"
RDRAM = 0x800000


def decomp(stub=STUB, vi=VI_INIT):
    root = Path(tempfile.mkdtemp())
    for name, text in {"sdk/BOOT.s": stub, "include/PR/R4300.h": R4300, "include/PR/os_convert.h": CONVERT,
                       "include/PR/os_system.h": SYSTEM, "sdk/libultra/io/vi/vi.CPP": vi,
                       "versions/us-rev1/libultra_names.tsv": NAMES, "symbols.txt": SYMBOLS}.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


def host_read_keys() -> set[tuple[str, ...]]:
    """Every facts key path the host reads, collected from hosts/n64recomp/CMakeLists.txt's string(JSON) calls and the
    profile scripts' subscripts of `facts` (and of the names bound to a part of it); `*` is each list element."""
    keys: set[tuple[str, ...]] = set()
    for call in re.finditer(r'string\(JSON\s+\S+\s+(?:GET|LENGTH)\s+"\$\{_pj\}"\s+([^)]+)\)',
                            (HOSTS / "CMakeLists.txt").read_text()):
        keys.add(tuple("*" if token.startswith("${") else token for token in call.group(1).split()))
    for script in ("boot_profile.py", "cpu_profile.py", "tlb_profile.py", "input_profile.py"):
        tree = ast.parse((HOSTS / script).read_text())
        alias: dict[str, tuple[str, ...]] = {}

        def path(node, alias=alias):
            parts = []
            while (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
               and isinstance(node.slice.value, str)):
                parts.append(node.slice.value)
                node = node.value
            if isinstance(node, ast.Name) and (node.id == "facts" or node.id in alias):
                return (alias.get(node.id, ()) + tuple(reversed(parts)), node)
            return None

        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                    and (found := path(node.value))):
                alias[node.targets[0].id] = found[0]
            elif (isinstance(node, (ast.For, ast.comprehension)) and isinstance(node.target, ast.Name)
                    and (found := path(node.iter))):
                alias[node.target.id] = found[0] + ("*",)
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and (found := path(node)) and found[0]:
                keys.add(found[0])
    return keys


class FactsTests(unittest.TestCase):
    def boot(self, root, version="us-rev1", rdram=RDRAM):
        return boot_facts(root, root / "symbols.txt", version, rdram)

    def test_boot_facts_come_from_the_entry_stub_and_the_symbol_list(self):
        boot = self.boot(decomp())
        self.assertEqual(boot["sp"], 0x803FFFC0)
        self.assertEqual(boot["tlb"], [{"index": 0x1F, "page_size": 0x100000, "size": 0x100000,
                                        "vaddr": 0x200000, "paddr": 0}])
        self.assertEqual(boot["faulted_thread"], "0x800D92A4")
        self.assertEqual(boot["tlb_map_abi"], "entrylo_flags")

    def test_boot_main_is_the_kseg0_function_the_stub_jumps_to(self):
        # Sched_RestoreBootMemoryAddressUsRev1 = 0x002A81B0 (absolute); 0x802A81B0 is ItemMenu_RestoreBootMemory.
        self.assertEqual(self.boot(decomp())["main"], 0x802A81B0)
        with self.assertRaises(TasteError):
            self.boot(decomp(), version="de")  # no VERSION_DE branch loads a continuation
        with self.assertRaises(TasteError):
            self.boot(decomp(), version="eu")  # Sched_RestoreBootMemoryAddressEu is not in the symbol list

    def test_boot_addresses_and_status_are_read_not_typed(self):
        boot = self.boot(decomp())
        self.assertEqual(boot["os_clock_rate"], "0x8014FD90")  # g_systemClockFrequency, the decomp's osClockRate
        self.assertEqual(boot["vi_mode"], "0x800D8470")  # __osViInit's NTSC branch: osViModeTable
        self.assertEqual(boot["registers"]["status"], 0x24000000)  # SR_CU1 | SR_FR
        self.assertEqual(boot["rdram_size"], RDRAM)
        with self.assertRaises(TasteError):
            self.boot(decomp(vi=VI_INIT.replace("osViModeTable", "other")))
        with self.assertRaises(TasteError):
            self.boot(decomp(), rdram=None)

    def test_cpu_facts_read_the_count_rate_the_tv_rate_and_the_native_extents(self):
        root = decomp()
        cpu = cpu_facts(root, root / "symbols.txt", "us-rev1")
        self.assertEqual(cpu["timing"], {"count_hz": 46875000, "vi_hz": 60})  # OS_CLOCK_RATE*3/4, NTSC
        # each row runs to the next function start; touching extents merge (guLookAt, guLookAtF, then guMtxIdent)
        self.assertEqual(cpu["native_code"], [[0x8028F89C, 0x8028F92C], [0x802BB740, 0x802BBC50]])
        self.assertNotIn("calibration", cpu)

    def test_symbol_address_refuses_an_unnamed_symbol(self):
        self.assertEqual(symbol_address(SYMBOLS, "osMapTLB"), 0x802B2440)
        with self.assertRaises(TasteError):
            symbol_address(SYMBOLS, "osClockRate")

    def test_a_tlb_extent_that_is_not_one_or_two_pages_is_refused(self):
        with self.assertRaises(TasteError):
            self.boot(decomp(STUB.replace("lui     t1, 0x10", "lui     t1, 0x30")))

    def test_input_facts_read_the_tracked_bindings(self):
        self.assertEqual(input_facts(INPUT)["minimum_press_ms"], 80)
        with self.assertRaises(TasteError):
            input_facts(None)

    def test_every_host_read_key_is_derived(self):
        root = decomp()
        rom = root / "rom.z64"
        rom.write_bytes(bytes.fromhex("80371240 0000000F 80000400 00000000".replace(" ", "")))
        settings = SimpleNamespace(
            decomp_root=root, symbols_path=lambda version: root / "symbols.txt", rom=lambda version: rom,
            entrypoint=None, input_bindings=INPUT, rdram_size=RDRAM)
        section = SimpleNamespace(name="game_code", rom=0x1000, vram=0x80246000, size=0x100, overlay=False,
                                  functions=[1])
        with mock.patch.object(generate, "build", return_value=(SimpleNamespace(sha1="a" * 40), [section],
                                                                  {"functions": 1})), \
                mock.patch.object(generate, "boot_segment", return_value=section):
            facts = generate.oracle_facts(settings, "us-rev1")
        keys = host_read_keys()
        self.assertGreaterEqual(len(keys), 25, sorted(keys))  # the collector found the host's reads, not a stub
        for key in sorted(keys):
            with self.subTest(key="/".join(key)):
                self.assertTrue(self.present([facts], key), f"the host reads {'/'.join(key)} and facts hold none")
        self.assertIn(("boot", "main"), keys)
        self.assertIn(("cpu", "native_code"), keys)
        self.assertNotIn(("cpu", "calibration", "window"), keys)

    def present(self, nodes, key):
        if not key:
            return all(node is not None for node in nodes)
        head, rest = key[0], key[1:]
        children = []
        for node in nodes:
            if head == "*":
                if not isinstance(node, list) or not node:
                    return False
                children += node
            elif isinstance(node, dict) and node.get(head) is not None:
                children.append(node[head])
            else:
                return False
        return self.present(children, rest)


if __name__ == "__main__":
    unittest.main()
