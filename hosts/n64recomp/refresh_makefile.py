"""The decomp checkout's Makefile from the selected unbake's generator.

`refresh_makefile.py MAKEFILE` removes a Makefile another generator wrote, so setup's sync writes the current one.
`refresh_makefile.py MAKEFILE HOST` writes the current one itself whenever it is absent or another generator wrote it:
`unbake setup` extracts every version through `make extract-<v>` before it syncs, so a checkout without a Makefile (a
fresh clone of the PINS commit, or one whose old Makefile was just removed) refuses `setup.splat` (no rule to make
target extract-<v>) and setup never reaches the sync that would write one."""

import sys
from pathlib import Path

from unbake.build import generator_of, generator_version


def refresh(path: Path, host: Path | None = None) -> None:
    if path.is_file() and generator_of(path.read_text()) == generator_version():
        return
    if host is None:
        path.unlink(missing_ok=True)
        return
    from unbake import build, config

    path.write_bytes(build.makefile(config.load(path.parent, host)))


if __name__ == "__main__":
    refresh(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
