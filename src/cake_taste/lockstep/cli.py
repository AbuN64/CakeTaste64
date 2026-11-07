"""`taste lockstep roots --match REPORT [--out FILE] [--against KEPT [--root ID ...]] [--members N]`.

The census of a lockstep match report reduced to its roots (lockstep/roots.py), from the map the profile's `lockstep`
section names (`{"map": PATH}`, owned by the project). With `--against` the report is judged against a census kept
from before: each named root must be gone and no new root may start at or before its `through`, else the command
refuses (`lockstep.present`, `lockstep.new`) after writing `--out`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from cake_taste import TasteError
from cake_taste.files import atomic_json

from . import roots

SECTION_KEYS = frozenset({"map"})


def _read(path: Path, key: str) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise TasteError(key, f"{path}: {error}") from error


def _map(profile: Any) -> roots.Map:
    section = profile.section("lockstep")
    if extra := section.keys() - SECTION_KEYS:
        raise TasteError("lockstep.profile", f"unknown keys: {', '.join(sorted(extra))}")
    if "map" not in section:
        raise TasteError("lockstep.profile", "lockstep.map names the project's class map")
    return roots.read_map(profile.resolve_path(section["map"]))


def _capped(result: dict[str, Any], members: int) -> dict[str, Any]:
    def cap(census: dict[str, Any]) -> dict[str, Any]:
        return {**census, "roots": [{**e, "members": e["members"][:members]} for e in census["roots"]]}

    return {**result, "census": cap(result["census"])} if "census" in result else cap(result)


def run(profile: Any, argv: tuple[str, ...]) -> dict[str, Any]:
    parser = argparse.ArgumentParser(prog="taste lockstep")
    commands = parser.add_subparsers(dest="action", required=True)
    found = commands.add_parser("roots", help="the roots of a match report and the consequences each carries")
    found.add_argument("--match", type=Path, required=True, help="the match report (divergences: class -> rows)")
    found.add_argument("--out", type=Path, help="write the whole result here (every member: --against reads it)")
    found.add_argument("--against", type=Path, help="a census kept with --out: judge each root's state now")
    found.add_argument("--root", action="append", default=[], help="with --against: the root a fix must remove")
    found.add_argument("--members", type=int, default=8, help="consequences listed per root on stdout")
    args = parser.parse_args(argv)
    if args.root and not args.against:
        raise TasteError("cli.arguments", "--root needs --against (the census the root was named in)")
    spec = _map(profile)
    report = _read(args.match, "lockstep.report")
    if args.against is None:
        result = {"match": str(args.match), **roots.census(report, spec)}
    else:
        judged = roots.verify(report, spec, _read(args.against, "lockstep.against"), args.root)
        result = {"match": str(args.match), "against": str(args.against), **judged}
    if args.out:
        atomic_json(args.out, result, sort_keys=True)
    if args.root:
        left = [r for r in result["roots"] if r["state"] in ("present", "moved")]
        unseen = [r for r in result["roots"] if r["state"] == "beyond"]
        if left or unseen:
            raise TasteError(
                "lockstep.present",
                "; ".join(
                    f"{r['id']} {r['state']}" + (f" at poll {r['poll']}" if r["poll"] is not None else "")
                    for r in left + unseen
                ),
            )
        through = max(r["through"] for r in result["roots"])
        if early := [n for n in result["new"] if n["poll"] <= through]:
            raise TasteError(
                "lockstep.new", "; ".join(f"new {n['status']} {n['id']}" for n in early) + f" (through {through})"
            )
    return _capped(result, args.members)
