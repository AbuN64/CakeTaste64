"""The golden store: the oracle's lockstep record of a movie, kept once per (ROM sha1, oracle build key, movie sha256).

The oracle is deterministic under its virtual clock, so it runs only when its build or the movie changes; every other
request is served from the store. Every path is found from the repository itself (the main working tree's
.state/oracle/live for the build, the RageWarsDecomp working copy beside it for the cartridge), never from the
environment.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from abucache import stages as cache_stages

from cake_taste import TasteError
from cake_taste.files import atomic_json, sha256_file

RECORD = "lockstep.jsonl"
FACTS = "record.json"
VERSION = "us-rev1"
# What the movie does not say but the record depends on; a request for the same address with other values is refused.
PARAMS = ("pak", "pads", "exit_after", "calls")

Runner = Callable[[list[str], Mapping[str, str], Path], int]


@dataclass(frozen=True)
class Layout:
    host: Path  # the live oracle binary
    key: str  # the live build's key file content
    rom: Path
    store: Path


def discover(root: Path, version: str = VERSION) -> Layout:
    """The oracle build, ROM and store of the repository at `root`: the main working tree's .state (the same folder
    for a checkout and for any worktree of it) and the RageWarsDecomp working copy beside it, the way ci/oracle does."""
    git = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    if git.returncode:
        raise TasteError("oracle.root", f"{root} is not in a git repository: {git.stderr.strip()}")
    main = Path(git.stdout.strip()).parent
    live = main / ".state" / "oracle" / "live"
    host, key = live / "n64host", live / "key"
    rom = main.parent / "RageWarsDecomp" / "roms" / f"baserom.{version}.z64"
    for need, step in (
        (host, "ci/oracle build"),
        (key, "ci/oracle build"),
        (rom, "clone RageWarsDecomp beside this repository"),
    ):
        if not need.is_file():
            raise TasteError("oracle.missing", f"{need} is missing; {step}")
    return Layout(host, key.read_text().strip(), rom, main / ".state" / "oracle-records")


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def folder(layout: Layout, rom_sha1: str, movie_sha256: str) -> Path:
    return layout.store / rom_sha1 / layout.key / movie_sha256


def host_env(layout: Layout, state: Path, movie: Path, out: Path, params: Mapping[str, Any]) -> dict[str, str]:
    """The oracle host's environment for one movie, the same one ci/oracle selfcheck plays under."""
    return {
        "SDL_VIDEODRIVER": "offscreen",
        "SDL_AUDIODRIVER": "dummy",
        "VK_DRIVER_FILES": "/usr/share/vulkan/icd.d/lvp_icd.json",
        "N64R_ROM": str(layout.rom),
        "N64R_CONFIG": str(state / "cfg"),
        "N64R_AUDIO_OUT": "/dev/null",
        "N64R_VIEW": str(out / "view.bmp"),
        "N64R_TAS_SHOT_EVERY": "0",
        "N64R_PAK": str(params["pak"]),
        "N64R_PADS": str(params["pads"]),
        "N64R_TAS_PLAY": str(movie),
        "N64R_TAS_EXIT_AFTER": str(params["exit_after"]),
        "N64R_LOCKSTEP_DIR": str(out),
        "N64R_LOCKSTEP_CALLS": str(params["calls"]),
    }


def run_host(argv: list[str], env: Mapping[str, str], cwd: Path) -> int:
    base = {k: v for k, v in os.environ.items() if k != "N64R_CPAK"}
    with (cwd / "oracle.txt").open("w") as output:
        return subprocess.run(
            argv, env={**base, **env}, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, check=False
        ).returncode


def serve(source: Path, into: Path | None) -> None:
    """The stored record, linked (or copied across file systems) into `into`/lockstep.jsonl."""
    if into is None:
        return
    into.mkdir(parents=True, exist_ok=True)
    target = into / RECORD
    target.unlink(missing_ok=True)
    try:
        os.link(source / RECORD, target)
    except OSError:
        shutil.copyfile(source / RECORD, target)


def record(
    layout: Layout,
    movie: Path,
    params: Mapping[str, Any],
    into: Path | None = None,
    runner: Runner = run_host,
) -> dict[str, Any]:
    """The oracle record of `movie`, from the store when it holds one, else recorded by the oracle and stored."""
    with cache_stages.timed("cake_taste.oracle.record") as began:
        return _record(layout, movie, params, into, runner, began)


def _record(
    layout: Layout, movie: Path, params: Mapping[str, Any], into: Path | None, runner: Runner, began: Any
) -> dict[str, Any]:
    rom_sha1, movie_sha256 = sha1_file(layout.rom), sha256_file(movie)
    final = folder(layout, rom_sha1, movie_sha256)
    address = {"rom_sha1": rom_sha1, "oracle_key": layout.key, "movie_sha256": movie_sha256}
    if (final / FACTS).is_file():
        facts = json.loads((final / FACTS).read_text())
        if any(facts["params"][name] != params[name] for name in PARAMS):
            raise TasteError(
                "oracle.params",
                f"{final} holds a record made with {facts['params']}, not {dict(params)}; "
                "the store keeps one per address",
            )
        serve(final, into)
        return {
            **address,
            "served": True,
            "path": str(final / RECORD),
            "bytes": facts["bytes"],
            "seconds": round(began.seconds, 3),
            "recorded_seconds": facts["seconds"],
        }
    layout.store.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".record-", dir=layout.store))
    try:
        state = layout.host.parents[2]
        env = host_env(layout, state, movie, work, params)
        with cache_stages.timed("cake_taste.oracle.host") as ran:
            code = runner([str(layout.host)], env, work)
        seconds = round(ran.seconds, 3)
        if code:
            tail = (
                (work / "oracle.txt").read_text(errors="replace").strip().splitlines()[-5:]
                if (work / "oracle.txt").is_file()
                else []
            )
            raise TasteError("oracle.failed", f"the oracle exited {code}: {' | '.join(tail)}")
        stored = work / RECORD
        if not stored.is_file() or stored.stat().st_size == 0:
            raise TasteError(
                "oracle.empty", f"the oracle exited 0 but wrote no {RECORD}: an empty record is not a golden"
            )
        size = stored.stat().st_size
        atomic_json(
            work / FACTS, {**address, "params": dict(params), "bytes": size, "seconds": seconds}, sort_keys=True
        )
        for extra in set(p.name for p in work.iterdir()) - {RECORD, FACTS}:
            (work / extra).unlink() if (work / extra).is_file() else shutil.rmtree(work / extra)
        final.parent.mkdir(parents=True, exist_ok=True)
        work.rename(final)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    serve(final, into)
    return {
        **address,
        "served": False,
        "path": str(final / RECORD),
        "bytes": size,
        "seconds": round(began.seconds, 3),
        "recorded_seconds": seconds,
    }
