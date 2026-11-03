"""Private CI environment keyed by pyproject and every source dependency's landed revision.

Uses only the standard library; never imports or borrows a globally installed toolkit.
"""

import hashlib
import re
import subprocess
import sys
import tomllib
from pathlib import Path

VENV = ".venv"
STAMP = ".pyproject.sha256"


def requirements(root: Path, run=None) -> list[str]:
    """pyproject.toml's dependencies and dev extra with each of our tools resolved to its landed commit."""
    project = tomllib.loads((root / "pyproject.toml").read_text())
    sources = project["tool"]["bootstrap"]["sources"]
    rows = [*project["project"]["dependencies"], *project["project"]["optional-dependencies"]["dev"]]
    resolved = []
    for row in rows:
        if row in sources:
            argv = ["git", "ls-remote", sources[row], "HEAD"]
            if run is None:
                done = subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False)
                code, output = done.returncode, done.stdout + done.stderr
            else:
                code, output = run(argv, root)
            found = re.search(r"^([0-9a-f]{40})\tHEAD$", output, re.MULTILINE)
            if code or not found:
                raise RuntimeError(f"{row}: git ls-remote HEAD failed: {output.strip()}")
            row = f"{row} @ git+{sources[row]}@{found[1]}"
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


def ensure(root: Path, run=subprocess.run, version: tuple[int, int] = sys.version_info[:2], lookup=None) -> Path:
    """The interpreter of the environment for `root`'s pyproject.toml, made or rebuilt first when its stamp differs."""
    venv = root / VENV
    python = venv / "bin" / "python"
    stamp = venv / STAMP
    rows = requirements(root, lookup)
    digest = key(root, rows)
    if python.exists() and stamp.exists() and stamp.read_text() == digest:
        return python
    need_python(root, version)
    run([sys.executable, "-m", "venv", "--clear", str(venv)], check=True, stdout=sys.stderr)
    run([str(python), "-m", "pip", "install", "--quiet", *rows], check=True, stdout=sys.stderr)
    run([str(python), "-m", "pip", "install", "--quiet", "--no-deps", "-e", str(root)], check=True, stdout=sys.stderr)
    stamp.write_text(digest)  # last: a failed install leaves no stamp, so the next run installs again
    return python


def main() -> int:
    sys.stdout.write(str(ensure(Path(__file__).resolve().parents[1])) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
