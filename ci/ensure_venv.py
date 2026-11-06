"""Private CI environment keyed by pyproject and every source dependency's resolved commit.

Uses only the standard library; never imports or borrows a globally installed toolkit.
"""

import hashlib
import re
import subprocess
import sys
import tomllib
from pathlib import Path

VENV = ".venv"
TOOL_VENV = ".venv-unbake"  # the oracle toolchain, refreshed with the same direct dependencies as the main environment
STAMP = ".pyproject.sha256"


SOURCE = re.compile(r"(https://\S+)@([0-9a-f]{40})")  # a source dependency: repository URL, "@", the exact commit


def requirements(root: Path, extras: tuple[str, ...] = ("dev", "oracle"), lookup=None) -> list[str]:
    """Resolve unpinned source HEADs to landed commits; explicit tool revisions remain exact."""
    project = tomllib.loads((root / "pyproject.toml").read_text())
    sources = project["tool"]["bootstrap"]["sources"]
    optional = project["project"]["optional-dependencies"]
    rows = [*project["project"]["dependencies"], *(row for extra in extras for row in optional[extra])]
    resolved = []
    for row in rows:
        if row in sources:
            found = SOURCE.fullmatch(sources[row])
            if found:
                url, commit = found[1], found[2]
            else:
                url = sources[row]
                argv = ["git", "ls-remote", url, "HEAD"]
                if lookup is None:
                    done = subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False)
                    code, output = done.returncode, done.stdout + done.stderr
                else:
                    code, output = lookup(argv, root)
                head = re.search(r"^([0-9a-f]{40})\tHEAD$", output, re.MULTILINE)
                if code or not head:
                    raise RuntimeError(f"{row}: git ls-remote HEAD failed: {output.strip()}")
                commit = head[1]
            row = f"{row} @ git+{url}@{commit}"
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


def ensure(
    root: Path, run=subprocess.run, version: tuple[int, int] = sys.version_info[:2], tool: bool = False, lookup=None
) -> Path:
    """The interpreter of the environment for `root`'s pyproject.toml, made or rebuilt first when its stamp differs.

    Both environments resolve the project's direct sources alongside their tools, so the oracle and main
    environment install the same landed cache dependency."""
    venv = root / (TOOL_VENV if tool else VENV)
    python = venv / "bin" / "python"
    stamp = venv / STAMP
    rows = requirements(root, ("oracle",) if tool else ("dev",), lookup)
    digest = key(root, rows)
    if python.exists() and stamp.exists() and stamp.read_text() == digest:
        return python
    need_python(root, version)
    run([sys.executable, "-m", "venv", "--clear", str(venv)], check=True, stdout=sys.stderr)
    run([str(python), "-m", "pip", "install", "--quiet", *rows], check=True, stdout=sys.stderr)
    if not tool:
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
