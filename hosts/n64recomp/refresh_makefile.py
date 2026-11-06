"""Let setup regenerate a Makefile only when its owning tool's generator changed."""

import sys
from pathlib import Path

from unbake.build import generator_of, generator_version


def refresh(path: Path) -> None:
    if path.is_file() and generator_of(path.read_text()) != generator_version():
        path.unlink()


if __name__ == "__main__":
    refresh(Path(sys.argv[1]))
