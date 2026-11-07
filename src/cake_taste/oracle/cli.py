"""`taste oracle record --movie MOVIE [--into DIR]`: the oracle's lockstep record of a movie, from the golden store."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import store


def run(profile: Any, argv: tuple[str, ...]) -> dict[str, Any]:
    parser = argparse.ArgumentParser(prog="taste oracle")
    commands = parser.add_subparsers(dest="action", required=True)
    record = commands.add_parser("record")
    record.add_argument("--movie", type=Path, required=True, help="the poll-keyed movie (movie.raw), one line per poll")
    record.add_argument("--into", type=Path, help="link the record into DIR/lockstep.jsonl")
    record.add_argument("--version", default=store.VERSION)
    record.add_argument("--pak", default="on")
    record.add_argument("--pads", type=int, default=1)
    record.add_argument("--exit-after", type=int, help="polls to play (default: every line of the movie)")
    record.add_argument(
        "--calls", type=int, default=8, help="calls kept per (caller, function), the recorder's default"
    )
    record.add_argument(
        "--mask", default="", help="N64R_LOCKSTEP_MASK: LO-HI,... words the record hashes and dumps as zero"
    )
    args = parser.parse_args(argv)
    movie = args.movie.resolve()
    polls = args.exit_after or sum(1 for line in movie.read_text().splitlines() if line.strip())
    params = {"pak": args.pak, "pads": args.pads, "exit_after": polls, "calls": args.calls, "mask": args.mask}
    layout = store.discover(Path(__file__).resolve().parent, args.version)
    return store.record(layout, movie, params, args.into)
