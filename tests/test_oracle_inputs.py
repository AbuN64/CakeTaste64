"""The oracle's own inputs: the tracked RageWars profile and the unbake host config ci/oracle writes for itself."""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from importlib import resources
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
        self.assertEqual((found.rsp_segment, found.rsp_function, found.rsp_execution_vram),
                         ("rsp_audio_code", "rsp_audio", 0x04001000))

    def test_the_derived_profile_makes_the_relative_rom_absolute_beside_the_live_file(self):
        loaded = self.layout()
        self.assertEqual(recomp_profile.beside(str(loaded), "../../../RageWarsDecomp/roms/x.z64"),
                         str(loaded.parents[3] / "RageWarsDecomp/roms/x.z64"))

    def test_the_audio_function_is_the_one_the_host_cmake_compiles(self):
        self.assertIn("rsp/rsp_audio.cpp", (ROOT / "hosts/n64recomp/CMakeLists.txt").read_text())


class HostConfig(unittest.TestCase):
    schema = json.loads((resources.files("unbake.resources") / "schemas" / "host.schema.json").read_text())
    template = (resources.files("unbake.resources") / "templates" / "host.toml").read_text()

    def render(self, abumas="/state/tools/abumas", missing=frozenset()) -> dict:
        import tomllib

        text = host_config.render(self.template, Path("/state"), abumas, 12, host_config.memory_total(MEMINFO),
                                  400_000_000_000, tools(missing))
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
        done = subprocess.run([sys.executable, str(ROOT / "hosts/n64recomp/host_config.py"), str(tmp / "unbake.toml"),
                               str(tmp / "state"), "--abumas", str(tmp / "abumas")],
                              capture_output=True, text=True, check=False)
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
