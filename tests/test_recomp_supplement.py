import json
import tempfile
import tomllib
import unittest
from dataclasses import replace
from pathlib import Path

from test_recomp import build

from cake_taste import TasteError
from cake_taste.recomp.generate import _reason, _rsp, load_layout, rsp_dispatch_targets, write_symbols
from cake_taste.recomp.settings import load
from cake_taste.recomp.supplement import read_sections


class SupplementTests(unittest.TestCase):
    def row(self):
        return {"name": "late_code", "rom": 0x5800, "vram": 0x80600000, "size": 16,
                "source": "fixture disassembly, four instructions",
                "functions": [{"name": "late_fn", "offset": 0, "size": 16}]}

    def test_explicit_section_reaches_symbols_and_analysis_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = build(root)
            path = root / "sections.json"
            path.write_text(json.dumps([self.row()]))
            rom = bytearray((root / "rom.z64").read_bytes())
            rom[0x5800:0x5810] = bytes(range(16))
            (root / "rom.z64").write_bytes(rom)
            settings = replace(load(profile), function_sections={"v1": path})
            result = write_symbols(settings, "v1")
            tables = tomllib.loads(Path(result["path"]).read_text())["section"]
            extra = next(s for s in tables if s["name"] == "late_code")
            self.assertEqual(extra["functions"], [{"name": "late_fn", "vram": 0x80600000, "size": 16}])
            flat = (root / "gen/v1/rom.flat").read_bytes()
            self.assertEqual(flat[extra["rom"]:extra["rom"] + 16], bytes(range(16)))
            self.assertEqual(result["functions"], 6)

    def test_missing_entry_can_be_added_inside_an_existing_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = build(root)
            path = root / "sections.json"
            row = {"name": "boot_code", "rom": 0x1000, "vram": 0x80200400, "size": 0x1000,
                   "source": "fixture instruction boundaries", "functions": [
                       {"name": "missing", "offset": 0x20, "size": 8}]}
            path.write_text(json.dumps([row]))
            settings = replace(load(profile), function_sections={"v1": path})
            result = write_symbols(settings, "v1")
            self.assertEqual(result["functions"], 6)
            tables = tomllib.loads(Path(result["path"]).read_text())["section"]
            boot = next(t for t in tables if t["name"] == "boot_code")
            self.assertEqual(boot["functions"][-1], {"name": "missing", "vram": 0x80200420, "size": 8})
            row["vram"] += 4
            path.write_text(json.dumps([row]))
            with self.assertRaises(TasteError):
                write_symbols(settings, "v1")

    def test_invalid_boundaries_and_missing_source_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sections.json"
            for mutate in (lambda r: r.update(source=""), lambda r: r.update(size=3),
                           lambda r: r["functions"][0].update(size=20),
                           lambda r: r["functions"].append(dict(r["functions"][0]))):
                row = self.row()
                mutate(row)
                path.write_text(json.dumps([row]))
                with self.assertRaises(TasteError):
                    read_sections(path)

    def test_function_extent_is_explicit_and_stays_inside_owning_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = replace(load(build(root)), function_sizes={"v1": {0x80200400: 16}})
            result = write_symbols(settings, "v1")
            functions = tomllib.loads(Path(result["path"]).read_text())["section"][0]["functions"]
            self.assertEqual(functions[0]["size"], 16)
            self.assertEqual(functions[1]["vram"], 0x80200408)
            for override in ({0x80200400: 0x1004}, {0x80900000: 16}):
                with self.assertRaises(TasteError):
                    write_symbols(replace(settings, function_sizes={"v1": override}), "v1")

    def test_rsp_dispatch_targets_reach_recompiler_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = replace(load(build(root)), rsp_indirect_targets=(0x1118, 0x12D0))
            _rsp(settings, "v1", load_layout(settings, "v1"))
            config = tomllib.loads((root / "gen/v1/rsp.toml").read_text())
            self.assertEqual(config["extra_indirect_branch_targets"], [0x1118, 0x12D0])

    # RageWars us-rev1 audio ucode: the dispatcher (add r2,r0,r1; lh r2,0x10(r2); jr r2; code rom 0xDFE30, IMEM
    # 0x1088) and the first 0x40 bytes of its DMEM data (rom 0xE1460): the acmd jump table at 0x10, then masks.
    DISPATCH = bytes.fromhex("00011020" "84420010" "00400008" "00000000")
    DATA = bytes.fromhex("0000 0001 0002 ffff 0020 0800 7fff 4000 1118 1470 11dc 1b38 1214 187c 1254 12d0"
                         "12ec 1328 140c 1294 1e24 138c 170c 144c f000 0f00 00f0 000f 0001 0010 0100 1000")

    def test_rsp_jump_table_targets_are_read_from_the_ucode_data(self):
        code = self.DISPATCH + bytes(0xE20 - len(self.DISPATCH))  # IMEM 0x1000..0x1E1F
        table = [0x1118, 0x1470, 0x11DC, 0x1B38, 0x1214, 0x187C, 0x1254, 0x12D0,
                 0x12EC, 0x1328, 0x140C, 0x1294, 0x1E24, 0x138C, 0x170C, 0x144C]
        self.assertEqual(rsp_dispatch_targets(code, self.DATA, 0x04001000), sorted(set(table) - {0x1E24}))
        self.assertEqual(rsp_dispatch_targets(bytes(64), self.DATA, 0x04001000), [])  # no dispatcher, no table

    def test_rsp_data_segment_is_a_layout_segment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = load(build(root, rsp_config={"segment": "rsp_code", "function": "rsp_fn",
                                                    "data_segment": "rsp_code"}))
            self.assertEqual(settings.rsp_data_segment, "rsp_code")
            with self.assertRaises(TasteError):
                _rsp(replace(settings, rsp_data_segment="absent"), "v1", load_layout(settings, "v1"))

    def test_rsp_execution_is_independent_of_storage_address(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = replace(load(build(root)), rsp_execution_vram=0x04001100)
            _rsp(settings, "v1", load_layout(settings, "v1"))
            config = tomllib.loads((root / "gen/v1/rsp.toml").read_text())
            self.assertEqual(config["text_address"], 0x04001100)
            with self.assertRaises(TasteError):
                _rsp(replace(settings, rsp_execution_vram=0xDFE30), "v1", load_layout(settings, "v1"))

    def test_refusal_keeps_the_concrete_cause(self):
        log = "No function found for jal target: 0x80600000\n"
        log += "Error in recompiling root, clearing output file\nError recompiling root\n"
        self.assertEqual(_reason(log, "root"), "No function found for jal target: 0x80600000")


if __name__ == "__main__":
    unittest.main()
