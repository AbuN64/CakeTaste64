"""The selfcheck's loader-directory search is shared, ordered and independent of manifest filenames."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cake_taste.oracle import icd


class Discovery(unittest.TestCase):
    def test_searches_directories_in_order_and_manifests_in_sorted_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            other = root / "a.json"
            other.write_text('{"ICD": {"library_path": "libother.so"}}')
            first = root / "b.json"
            first.write_text('{"ICD": {"library_path": "libvulkan_lvp.so"}}')
            second = root / "c.json"
            second.write_text(first.read_text())
            with (
                patch.dict(os.environ, {"XDG_DATA_DIRS": str(root) + ":/unused"}),
                patch.object(Path, "glob", return_value=iter([second, other, first])) as glob,
            ):
                self.assertEqual(icd.discover(), first)
            glob.assert_called_once_with("*.json")

    def test_absent_driver_refuses_after_all_loader_directories(self):
        searched = []

        def empty(directory, pattern):
            searched.append(directory)
            return iter(())

        with (
            patch.dict(os.environ, {"XDG_DATA_DIRS": "/first:/second"}),
            patch.object(Path, "glob", empty),
            self.assertRaisesRegex(FileNotFoundError, "install the lavapipe Vulkan driver"),
        ):
            icd.discover()
        self.assertEqual(searched, [Path(p) / "vulkan/icd.d" for p in ("/first", "/second", "/etc")])

    def test_unset_or_empty_data_dirs_use_loader_defaults(self):
        for env in ({}, {"XDG_DATA_DIRS": ""}):
            with self.subTest(env=env):
                searched = []

                def empty(directory, pattern, searched=searched):
                    searched.append(directory)
                    return iter(())

                with (
                    patch.dict(os.environ, env, clear=True),
                    patch.object(Path, "glob", empty),
                    self.assertRaises(FileNotFoundError),
                ):
                    icd.discover()
                self.assertEqual(
                    searched, [Path(p) / "vulkan/icd.d" for p in ("/usr/local/share", "/usr/share", "/etc")]
                )
