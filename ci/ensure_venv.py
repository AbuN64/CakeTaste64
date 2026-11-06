"""Private CI environment keyed by pyproject and every source dependency's pinned commit.

Uses only the standard library; never imports or borrows a globally installed toolkit.
"""

import hashlib
import re
import subprocess
import sys
import tomllib
from pathlib import Path

VENV = ".venv"
TOOL_VENV = ".venv-unbake"  # the decomp toolchain's own environment: unbake refuses any abucache but its pinned one
STAMP = ".pyproject.sha256"


SOURCE = re.compile(r"(https://\S+)@([0-9a-f]{40})")  # a source dependency: repository URL, "@", the exact commit


def requirements(root: Path, extras: tuple[str, ...] = ("dev", "oracle")) -> list[str]:
    """pyproject.toml's dependencies and the named extras, each of our tools pinned to the exact commit its
    `[tool.bootstrap.sources]` row names (`URL@COMMIT`); a row without one refuses, so no tool floats."""
    project = tomllib.loads((root / "pyproject.toml").read_text())
    sources = project["tool"]["bootstrap"]["sources"]
    optional = project["project"]["optional-dependencies"]
    rows = [*project["project"]["dependencies"], *(row for extra in extras for row in optional[extra])]
    resolved = []
    for row in rows:
        if row in sources:
            found = SOURCE.fullmatch(sources[row])
            if not found:
                raise RuntimeError(f"{row}: tool.bootstrap.sources must be URL@<40-hex commit>, not {sources[row]!r}")
            row = f"{row} @ git+{found[1]}@{found[2]}"
        resolved.append(row)
    return resolved


def key(root: Path, rows: list[str]) -> str:
    return hashlib.sha256(
        str(root.resolve()).encode() + b"\0" + (root / "pyproject.toml").read_bytes() + "\n".join(rows).encode()
    ).hexdigest()


def need_python(root: Path, version: tuple[int, int]) -> None:
    """Refuse an interpreter older than the pyproject's `requires-python = ">=X.Y"` before making an environment."""
    declared = tomllib.loads((root / "pyproject.toml").read_text())["project"].get("requires-python", "")
    found = re.fullmatch(r">=\s*(\d+)\.(\d+)", declared)
    if found and version < (int(found[1]), int(found[2])):
        raise SystemExit(f"bin/test: python3 is {version[0]}.{version[1]} but pyproject.toml needs {declared}")


def ensure(root: Path, run=subprocess.run, version: tuple[int, int] = sys.version_info[:2], tool: bool = False) -> Path:
    """The interpreter of the environment for `root`'s pyproject.toml, made or rebuilt first when its stamp differs.

    The default environment holds our own dependencies and the dev tools. `tool` is the unbake environment: only the
    `oracle` extra, so unbake brings the exact abucache commit it requires (it refuses any other), apart from the
    newer abucache our own code needs."""
    venv = root / (TOOL_VENV if tool else VENV)
    python = venv / "bin" / "python"
    stamp = venv / STAMP
    rows = requirements(root, ("oracle",) if tool else ("dev",))
    own = len(tomllib.loads((root / "pyproject.toml").read_text())["project"]["dependencies"])
    if tool:
        rows, own = rows[own:], 0
    digest = key(root, rows)
    if python.exists() and stamp.exists() and stamp.read_text() == digest:
        return python
    need_python(root, version)
    run([sys.executable, "-m", "venv", "--clear", str(venv)], check=True, stdout=sys.stderr)
    # The tools first, with the dependencies they pin; then our own dependencies at the commits this pyproject pins,
    # which win over a tool's older pin of the same package (a resolver run over both would refuse the two commits).
    run([str(python), "-m", "pip", "install", "--quiet", *rows[own:]], check=True, stdout=sys.stderr)
    if not tool:
        pin = ["--quiet", "--no-deps", "--force-reinstall"]
        run([str(python), "-m", "pip", "install", *pin, *rows[:own]], check=True, stdout=sys.stderr)
        editable = ["--quiet", "--no-deps", "-e", str(root)]
        run([str(python), "-m", "pip", "install", *editable], check=True, stdout=sys.stderr)
    stamp.write_text(digest)  # last: a failed install leaves no stamp, so the next run installs again
    return python


def main() -> int:
    tool = sys.argv[1:] == ["unbake"]
    if sys.argv[1:] and not tool:
        raise SystemExit("usage: ensure_venv.py [unbake]")
    sys.stdout.write(str(ensure(Path(__file__).resolve().parents[1], tool=tool)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
