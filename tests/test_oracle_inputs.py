"""The oracle's own inputs: the tracked RageWars profile and the unbake host config ci/oracle writes for itself."""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import jsonschema

from cake_taste.config import load_profile
from cake_taste.recomp import settings

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "hosts/n64recomp/ragewars.profile.json"
sys.path.insert(0, str(ROOT / "hosts/n64recomp"))
import host_config  # noqa: E402
import recomp_profile  # noqa: E402

MEMINFO = "MemTotal:       24576000 kB\nMemFree:  1 kB\n"


def tools(missing: frozenset[str] = frozenset()):
    return lambda binary: None if binary in missing else f"/usr/bin/{binary}"


class RageWarsProfile(unittest.TestCase):
    def layout(self) -> Path:
        """CakeTaste64/hosts/n64recomp/ragewars.profile.json beside a RageWarsDecomp folder, as the workspace has it."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "RageWarsDecomp").mkdir()
        target = tmp / "CakeTaste64/hosts/n64recomp"
        target.mkdir(parents=True)
        return Path(shutil.copy(PROFILE, target))

    def test_it_loads_through_profile_with_every_path_inside_the_workspace(self):
        loaded = self.layout()
        profile = load_profile(loaded)
        workspace = loaded.parents[3]
        self.assertEqual(profile.project_root, workspace / "RageWarsDecomp")
        self.assertEqual(profile.runtime_root, workspace / "CakeTaste64/.state/taste-run")
        text = PROFILE.read_text()
        for site in ("/home/", "/mnt/", "$HOME", "~", "http"):
            self.assertNotIn(site, text)
        roms = {"us-rev1": "../../../RageWarsDecomp/roms/baserom.us-rev1.z64"}
        self.assertEqual(json.loads(text)["recomp"]["roms"], roms)

    def test_the_recomp_section_is_valid_and_names_the_decomp_files_of_the_version(self):
        loaded = self.layout()
        found = settings.load(load_profile(loaded))
        workspace = loaded.parents[3]
        self.assertEqual(found.symbols_path("us-rev1"), workspace / "RageWarsDecomp/versions/us-rev1/symbol_addrs.txt")
        self.assertEqual(found.elf_path("us-rev1"), workspace / "RageWarsDecomp/build/us-rev1/RageWars.elf")
        self.assertEqual(found.rom("us-rev1"), workspace / "RageWarsDecomp/roms/baserom.us-rev1.z64")
        self.assertEqual(
            (found.rsp_segment, found.rsp_function, found.rsp_execution_vram, found.rsp_boot_segment),
            ("rsp_audio_code", "rsp_audio", None, "rsp_boot"),  # the load address is read from the boot ucode
        )

    def test_the_generated_functions_include_the_host_address_map_so_kuseg_loads_translate(self):
        """Regression: without recomp_include every generated load was N64Recomp's plain rdram + address, so the
        first KUSEG
        access of boot-idle (Sound_InitializeContext: lw v1, 4(v0) with v0 = 0x00800000, the pager's page 0x800 reply)
        read 0x80800004 bytes past the RDRAM base and segfaulted. probe.h redefines MEM_* through the host
        address map, and
        needs recomp.h before it (addrmap.h's unaligned accessors hang on __RECOMP_H__)."""
        found = settings.load(load_profile(self.layout()))
        self.assertEqual(found.recomp_include.splitlines(),
                         ['#include "recomp.h"', '#define N64HOST_RECORD_ONLY 1', '#include "probe.h"'])
        self.assertTrue((ROOT / "hosts/n64recomp/probe/probe.h").is_file())
        text = (ROOT / "hosts/n64recomp/probe/addrmap.h").read_text()
        for macro in ("MEM_W", "MEM_H", "MEM_B", "MEM_HU", "MEM_BU", "SD", "LD"):
            self.assertIn(f"#define {macro}(", text)

    def test_the_oracle_records_every_entry_without_charging_it(self):
        """Regression: the profile had no trace_mode, so N64Recomp emitted no TRACE_ENTRY and the lockstep recorder
        never ran: a 13,610-poll pp-1p-match record held 0 hit and 0 call lines, and the port's calls class was never
        compared. Trace mode with N64HOST_RECORD_ONLY gives the recorders every entry and return while no entry charges
        guest time, services interrupts or writes N64R_TRACE, so the game runs as it did without trace mode."""
        found = settings.load(load_profile(self.layout()))
        self.assertTrue(found.trace_mode)
        self.assertIn("#define N64HOST_RECORD_ONLY 1", found.recomp_include.splitlines())
        probe = ROOT / "hosts/n64recomp/probe"
        with tempfile.TemporaryDirectory() as tmp:
            unit = Path(tmp) / "unit.c"
            unit.write_text('#include <stdint.h>\n#include "trace.h"\nENTRY: TRACE_ENTRY()\nRETURN: TRACE_RETURN()\n'
                            "LOOP: TRACE_LOOP()\n")
            for record_only, wanted, absent in (
                (True, ("n64host_lockstep_entry", "n64host_lockstep_return", "n64host_replay_entry"),
                 ("n64host_trace", "n64host_cpu_tick", "n64host_charged")),
                (False, ("n64host_trace", "n64host_cpu_tick", "n64host_lockstep_entry"), ()),
            ):
                flags = ["-DN64HOST_RECORD_ONLY=1"] if record_only else []
                text = subprocess.run(["gcc", "-E", "-P", *flags, f"-I{probe}", str(unit)], capture_output=True,
                                      text=True, check=True).stdout
                body = text[text.index("ENTRY:"):]
                for name in wanted:
                    self.assertIn(name + "(", body, (record_only, name))
                for name in absent:
                    self.assertNotIn(name + "(", body, (record_only, name))

    def test_the_derived_profile_makes_the_relative_rom_absolute_beside_the_live_file(self):
        loaded = self.layout()
        self.assertEqual(
            recomp_profile.beside(str(loaded), "../../../RageWarsDecomp/roms/x.z64"),
            str(loaded.parents[3] / "RageWarsDecomp/roms/x.z64"),
        )

    # one name per HOST_KEPT family: a family that matches nothing stops the derivation
    KEPT_SAMPLES = ("osContInit", "osMotorInit", "osPfsInit", "osPiStartDma", "osCartRomInit", "osCreatePiManager")

    def derive(self, runtime_names, rom="/main/RageWarsDecomp/roms/baserom.us-rev1.z64"):
        """The derived profile for a pinned decomp, N64Recomp and runtime that all name `runtime_names`."""
        import contextlib
        import io

        loaded = self.layout()
        build = loaded.parents[3] / "build"
        names = sorted({*runtime_names, *self.KEPT_SAMPLES})
        (build / "deps/N64Recomp/src").mkdir(parents=True)
        (build / "deps/N64Recomp/src/symbol_lists.cpp").write_text(
            "reimplemented_funcs {\n" + "".join(f' "{n}",\n' for n in names) + "};\nignored_funcs {\n};\n"
        )
        (build / "deps/N64ModernRuntime").mkdir(parents=True)
        (build / "deps/N64ModernRuntime/runtime.cpp").write_text(
            "".join(f"void {n}_recomp(uint8_t* rdram, recomp_context* ctx) {{\n}}\n" for n in names)
        )
        symbols = build / "decomp/versions/us-rev1/symbol_addrs.txt"
        symbols.parent.mkdir(parents=True)
        symbols.write_text(
            "".join(f"{n} = 0x{0x80300000 + 0x100 * i:08X}; // type:func\n" for i, n in enumerate(names))
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            recomp_profile.main(str(loaded), str(build), "us-rev1", rom)
        return loaded, build, json.loads(out.getvalue())["recomp"]

    def test_the_derived_profile_names_the_rom_ci_oracle_found_not_a_path_beside_the_worktree(self):
        """A run worktree (Runs/<card>/Projects/AbuN64/CakeTaste64) has no RageWarsDecomp beside it, so the
        tracked profile's relative ROM path points at a file that is not there; the ROM ci/oracle derived from
        git-common-dir wins."""
        names = {"osViSwapBuffer", "osSpTaskStartGo", "osDpSetNextBuffer", "osVirtualToPhysical"}
        rom = "/main/RageWarsDecomp/roms/baserom.us-rev1.z64"
        loaded, build, derived = self.derive(names, rom)
        self.assertEqual(derived["roms"], {"us-rev1": rom})
        self.assertEqual({e["name"] for e in derived["symbol_names"]["us-rev1"].values()}, names)
        self.assertEqual(derived["decomp_root"], f"{build}/decomp")
        self.assertEqual(derived["input_bindings"], str(loaded.parent / "ragewars.input.json"))

    def test_the_generated_functions_see_the_declarations_of_the_functions_n64recomp_refused(self):
        """Libsn_* call Libgultra_TrwReadTlbEntry, which N64Recomp refused and so never declares: gcc 14 fails
        on the implicit declaration."""
        cmake = (ROOT / "hosts/n64recomp/CMakeLists.txt").read_text()
        self.assertIn(r'set(_stub_h "#pragma once\n#include \"recomp.h\"\n")', cmake)
        self.assertRegex(
            cmake,
            r'set_source_files_properties\(\$\{GEN_FUNCS\} PROPERTIES COMPILE_OPTIONS "[^"]*-include;\$\{CMAKE_BINARY_DIR\}/gen/refused_decls\.h[";]',  # noqa: E501
        )

    def test_the_recompile_step_key_reads_every_file_the_derived_profile_comes_from(self):
        """HOST_KEPT decides which libultra originals the recompile keeps from the runtime's: a change to it
        must redo the recompile."""
        oracle = (ROOT / "ci/oracle").read_text()
        key = next(line for line in oracle.splitlines() if line.strip().startswith("recompkey="))
        for name in ("HOST_KEPT", "ragewars.profile.json", "recomp_profile.py"):
            self.assertIn(name, key)

    def test_every_libultra_original_the_host_wraps_as_recomp_is_a_runtime_function_of_the_profile(self):
        """The host wraps `<name>_recomp` only for a function the recompile swaps for the runtime's (the derived
        symbol_names): left out, the original is kept as `<name>_kept`, the wrap never binds and the original runs. The
        boot-idle run segfaulted so, in __osSiDeviceBusy_kept under osInitialize_kept (SI_STATUS at 0xA4800018,
        unmapped). Only a HOST_KEPT family may keep a wrapped function."""
        cmake = (ROOT / "hosts/n64recomp/CMakeLists.txt").read_text()
        wrapped = set(re.findall(r"--wrap=(\w+)_recomp\b", cmake))
        for source in [*(ROOT / "hosts/n64recomp").glob("*.c"), *(ROOT / "hosts/n64recomp").glob("*.cpp")]:
            wrapped |= set(
                re.findall(
                    r"^(?:extern \"C\" )?void (?!__wrap_|__real_)(\w+)_recomp\(uint8_t", source.read_text(), re.M
                )
            )
        absent_from_the_decomp = {
            "__osInitialize_common",
            "osGetMemSize",
        }  # the decomp names neither; the wrap binds nothing
        wrapped -= absent_from_the_decomp
        self.assertIn("osInitialize", wrapped | {"osInitialize"})
        _, _, derived = self.derive(wrapped)
        runtime = {e["name"] for e in derived["symbol_names"]["us-rev1"].values()}
        self.assertEqual(wrapped - runtime, set())

    def test_the_generated_functions_see_the_declarations_of_the_runtime_functions_the_profile_renames_to(self):
        """A generated function that calls `<name>_recomp` (osUnmapTLBAll_recomp, defined by the runtime,
        declared by none of its
        headers) fails under gcc 14 as an implicit declaration unless the generated recomp_decls.h is force-included."""
        cmake = (ROOT / "hosts/n64recomp/CMakeLists.txt").read_text()
        self.assertRegex(
            cmake,
            r'set_source_files_properties\(\$\{GEN_FUNCS\} PROPERTIES COMPILE_OPTIONS "[^"]*-include;\$\{N64R_GEN_DIR\}/funcs/recomp_decls\.h"',  # noqa: E501
        )

    def test_the_host_declares_the_runtime_functions_whose_addresses_replaced_funcs_takes(self):
        """replaced_funcs.inl lists `<name>_recomp` for every profile runtime function; osUnmapTLBAll_recomp is
        defined by the
        runtime but declared by no header of it, so main.cpp failed to compile until it included the generated
        recomp_decls.h."""
        main = (ROOT / "hosts/n64recomp/main.cpp").read_text()
        self.assertIn('#include "recomp_decls.h"', main)
        self.assertLess(main.index('#include "recomp_decls.h"'), main.index('#include "replaced_funcs.inl"'))

    def test_the_audio_function_is_the_one_the_host_cmake_compiles(self):
        self.assertIn("rsp/rsp_audio.cpp", (ROOT / "hosts/n64recomp/CMakeLists.txt").read_text())


UNBAKE_RESOURCES = next(ROOT.glob(".venv-unbake/lib/python*/site-packages/unbake/resources"), None)


@unittest.skipIf(UNBAKE_RESOURCES is None, "no .venv-unbake in this tree: the repository check makes it")
class HostConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads((UNBAKE_RESOURCES / "schemas" / "host.schema.json").read_text())
        cls.template = (UNBAKE_RESOURCES / "templates" / "host.toml").read_text()

    def render(self, abumas="/state/tools/abumas", missing=frozenset()) -> dict:
        import tomllib

        text = host_config.render(
            self.template,
            Path("/state"),
            abumas,
            12,
            host_config.memory_total(MEMINFO),
            400_000_000_000,
            tools(missing),
        )
        self.assertNotIn("${", text)
        return tomllib.loads(text)

    def test_the_generated_config_validates_against_the_unbake_host_schema(self):
        config = self.render()
        jsonschema.validate(config, self.schema)
        self.assertEqual(config["tools"]["abumas"], "/state/tools/abumas")
        self.assertNotIn("n64link", config["tools"])
        self.assertEqual(config["cache"]["dir"], "/state/unbake-cache")
        self.assertEqual(config["toolchains"]["root"], "/state/toolchains")

    def test_resources_derive_from_the_host_not_from_literals(self):
        config = self.render()
        self.assertEqual((config["resources"]["cores"], config["resources"]["workers"]), ("auto", "auto"))
        self.assertEqual(config["resources"]["memory_parent_bytes"], 24576000 * 1024 // 16)
        self.assertEqual(config["resources"]["memory_worker_bytes"], 24576000 * 1024 // 24)
        self.assertEqual(config["cache"]["max_bytes"], 100_000_000_000)

    def test_an_optional_tool_that_is_not_found_is_left_out_and_the_config_still_validates(self):
        config = self.render(abumas=None, missing=frozenset({"armips"}))
        jsonschema.validate(config, self.schema)
        self.assertNotIn("abumas", config["tools"])
        self.assertNotIn("armips", config["tools"])

    def test_a_required_tool_off_the_path_stops_naming_it(self):
        with self.assertRaises(SystemExit) as stop:
            self.render(missing=frozenset({"splat", "mips-linux-gnu-as"}))
        self.assertIn("splat", str(stop.exception))
        self.assertIn("mips-linux-gnu-as", str(stop.exception))

    def test_the_command_writes_the_file_inside_the_state_folder(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        unbake_python = str(ROOT / ".venv-unbake/bin/python")
        done = subprocess.run(
            [
                unbake_python,
                str(ROOT / "hosts/n64recomp/host_config.py"),
                str(tmp / "unbake.toml"),
                str(tmp / "state"),
                "--abumas",
                str(tmp / "abumas"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode:
            self.assertIn("not on PATH", done.stderr)  # a machine without the mips binutils refuses by name
            return
        text = (tmp / "unbake.toml").read_text()
        self.assertTrue(re.search(rf'^dir = "{tmp}/state/unbake-cache"$', text, re.MULTILINE))


class OracleWiring(unittest.TestCase):
    def test_ci_oracle_builds_abumas_from_its_pin_and_passes_its_own_host_config(self):
        text = (ROOT / "ci/oracle").read_text()
        self.assertIn("hosts/n64recomp/ragewars.profile.json", text)
        self.assertIn('host_config.py "$host" "$state" --abumas "$state/tools/abumas"', text)
        self.assertIn('"$unbake" "$host" "$jobs"', text)
        self.assertRegex((ROOT / "hosts/n64recomp/PINS").read_text(), r"(?m)^AbuMas [0-9a-f]{40}$")
        deps = (ROOT / "hosts/n64recomp/deps.sh").read_text()
        self.assertIn("prepare AbuMas https://github.com/AbuN64/mas-n64", deps)
        self.assertNotIn(".config/unbake", text)


if __name__ == "__main__":
    unittest.main()
