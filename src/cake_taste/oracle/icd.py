"""Lavapipe discovery shared by the store and ci/oracle selfcheck.

The selfcheck runs this standard-library-only file with python3 to print the manifest path.
"""

import os
import re
from pathlib import Path


def discover() -> Path:
    """Find the first manifest naming lvp in the loader's data directories, then /etc."""
    dirs = (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    for directory in [*dirs, "/etc"]:
        if not directory:
            continue
        for manifest in sorted((Path(directory) / "vulkan/icd.d").glob("*.json")):
            if re.search(r'"library_path"[^,\n]*lvp', manifest.read_text()):
                return manifest
    raise FileNotFoundError(
        "no Vulkan ICD manifest naming lvp (lavapipe, the software driver the offscreen deterministic check needs) "
        "in the loader's vulkan/icd.d folders; install the lavapipe Vulkan driver"
    )


if __name__ == "__main__":
    try:
        print(discover())
    except OSError as error:
        raise SystemExit(f"selfcheck: {error}") from error
