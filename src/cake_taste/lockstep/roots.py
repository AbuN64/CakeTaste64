"""The roots of a lockstep census: which divergences start something and which are carried from an earlier one.

A census (a project's match report) lists, per class, every divergence row with the poll it shows at. Counted flat,
everything after the first divergence looks like work, so fixes go one at a time, each checked by a whole replay. Here
the rows are reduced to what can be worked on its own:

- an onset is the first row of one locus (the class's own key: a page, a (thread, caller, function), nothing for a
  per-poll scalar such as a clock); every later row of that locus is carried by it;
- an episode is onsets joined by a link the record itself shows, tried in this order: nesting (on the same thread, the
  onset's enclosing unit, its `within` field, is a unit an episode already diverged in, or the onset's unit encloses
  one), thread (the same thread diverged within `gap` polls), coincide (an onset within `coincide` polls of an
  episode's root of the same resolution; at one poll the upstream class, the earlier one in the map, is the root).
  Its root is its earliest onset; every other onset is a consequence named with its link;
- a root is `root` when no other resolved episode is active at its poll (nothing the record shows can have carried
  into it), `provisional` when one is (`upstream`: it may be that one's consequence through state no class records), and
  `unresolved` when its rows are of a resolution that cannot tell a difference from an equivalence (the map's
  `unresolved` match, such as a page hash without the page bytes): those need the bytes (`dump_polls`) before they
  count, and they never make another root provisional.

Each episode names `through` (its poll plus `margin`): a replay of the movie's first `through` polls shows it, so it is
checked without the whole movie. `verify` tells, against a census kept from before, which roots are gone, still
present or moved and which onsets are new, so a fix is proven on its own prefix while other fixes go on beside it.

The project owns the map (the classes in upstream order and each one's fields); nothing here knows a game or a port.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cake_taste import TasteError

MAP_VERSION = 1
MAP_KEYS = frozenset({"version", "classes", "gap", "coincide", "margin", "covered"})
CLASS_KEYS = frozenset({"name", "locus", "thread", "within", "unit", "unresolved"})
LINKS = ("nesting", "thread", "coincide")


@dataclass(frozen=True)
class ClassMap:
    name: str
    locus: tuple[str, ...]
    thread: str | None = None  # the field naming the thread (or any serial actor) a row ran on
    within: str | None = None  # the field naming the enclosing unit (a caller)
    unit: str | None = None  # the field naming the unit itself (a function)
    unresolved: Mapping[str, Any] = field(default_factory=dict)  # rows matching every pair are of unknown resolution


@dataclass(frozen=True)
class Map:
    classes: tuple[ClassMap, ...]
    gap: int = 60
    coincide: int = 1
    margin: int = 30
    covered: tuple[str, ...] = ()  # the report's path to the polls both sides compared (dotted in the file)


def _refuse(where: str, detail: str) -> TasteError:
    return TasteError("lockstep.map", f"{where}: {detail}")


def _count(where: str, value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise _refuse(where, f"{name} must be an integer >= 0")
    return value


def _field(where: str, value: Any, name: str) -> str | None:
    if value is not None and (not isinstance(value, str) or not value):
        raise _refuse(where, f"{name} must be a field name")
    return value


def parse_map(value: Any, where: str = "map") -> Map:
    if not isinstance(value, dict):
        raise _refuse(where, "expected an object")
    if value.get("version") != MAP_VERSION:
        raise _refuse(where, f"version must be {MAP_VERSION}")
    if extra := value.keys() - MAP_KEYS:
        raise _refuse(where, f"unknown keys: {', '.join(sorted(extra))}")
    rows = value.get("classes")
    if not isinstance(rows, list) or not rows:
        raise _refuse(where, "classes must be a nonempty list, upstream first")
    classes, seen = [], set()
    for number, row in enumerate(rows):
        at = f"{where}: classes[{number}]"
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"]:
            raise _refuse(at, "expected an object with a name")
        if extra := row.keys() - CLASS_KEYS:
            raise _refuse(at, f"unknown keys: {', '.join(sorted(extra))}")
        if row["name"] in seen:
            raise _refuse(at, f"class {row['name']} twice")
        seen.add(row["name"])
        locus = row.get("locus", [])
        if not isinstance(locus, list) or not all(isinstance(f, str) and f for f in locus):
            raise _refuse(at, "locus must be a list of field names")
        unresolved = row.get("unresolved", {})
        if not isinstance(unresolved, dict):
            raise _refuse(at, "unresolved must be an object of field: value")
        within = _field(at, row.get("within"), "within")
        thread = _field(at, row.get("thread"), "thread")
        if within and not thread:
            raise _refuse(at, "within needs thread: nesting is read on one thread")
        classes.append(
            ClassMap(row["name"], tuple(locus), thread, within, _field(at, row.get("unit"), "unit"), dict(unresolved))
        )
    covered = value.get("covered", "")
    if not isinstance(covered, str):
        raise _refuse(where, "covered must be a dotted path into the report")
    return Map(
        tuple(classes),
        _count(where, value.get("gap", 60), "gap"),
        _count(where, value.get("coincide", 1), "coincide"),
        _count(where, value.get("margin", 30), "margin"),
        tuple(part for part in covered.split(".") if part),
    )


def read_map(path: Path) -> Map:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise _refuse(str(path), str(error)) from error
    return parse_map(value, str(path))


@dataclass
class Onset:
    cls: str
    order: int  # the class's place in the map: lower is upstream
    locus: tuple[Any, ...]
    poll: int
    row: dict[str, Any]
    unresolved: bool
    thread: Any = None
    unit: Any = None
    within: Any = None
    rows: int = 0
    last: int = 0

    @property
    def key(self) -> tuple[Any, ...]:
        return (self.cls, *self.locus)

    @property
    def id(self) -> str:
        where = "/".join("-" if v is None else str(v) for v in self.locus)
        return f"{self.cls}@{self.poll}" + (f":{where}" if where else "")


@dataclass
class Episode:
    root: Onset
    members: list[tuple[Onset, str]] = field(default_factory=list)
    last: int = 0
    threads: set[Any] = field(default_factory=set)
    units: set[tuple[Any, Any]] = field(default_factory=set)
    withins: set[tuple[Any, Any]] = field(default_factory=set)

    def take(self, onset: Onset) -> None:
        if onset.unresolved:
            return  # an onset of unknown resolution is listed, never evidence that the episode goes on
        self.last = max(self.last, onset.last)
        if onset.thread is not None:
            self.threads.add(onset.thread)
            if onset.unit is not None:
                self.units.add((onset.thread, onset.unit))
            if onset.within is not None:
                self.withins.add((onset.thread, onset.within))


def _rows(report: Mapping[str, Any]) -> Mapping[str, Any]:
    rows = report.get("divergences") if isinstance(report, Mapping) else None
    if not isinstance(rows, Mapping):
        raise TasteError("lockstep.report", "the report holds no divergences object (class -> rows)")
    return rows


def onsets(report: Mapping[str, Any], spec: Map) -> list[Onset]:
    """Every locus's first row, in poll order (upstream class first at one poll), with its row count and last poll."""
    rows, found = _rows(report), {}
    if extra := set(rows) - {c.name for c in spec.classes}:
        raise TasteError("lockstep.report", f"classes the map does not name: {', '.join(sorted(extra))}")
    for order, cls in enumerate(spec.classes):
        listed = rows.get(cls.name, [])
        if not isinstance(listed, list):
            raise TasteError("lockstep.report", f"class {cls.name}: expected a list of rows")
        for number, row in enumerate(listed):
            if not isinstance(row, dict) or type(row.get("poll")) is not int:
                raise TasteError("lockstep.report", f"class {cls.name} row {number}: no integer poll")
            locus = tuple(row.get(name) for name in cls.locus)
            key = (cls.name, *locus)
            onset = found.get(key)
            if onset is None or row["poll"] < onset.poll:
                first = Onset(
                    cls.name,
                    order,
                    locus,
                    row["poll"],
                    row,
                    bool(cls.unresolved) and all(row.get(k) == v for k, v in cls.unresolved.items()),
                    row.get(cls.thread) if cls.thread else None,
                    row.get(cls.unit) if cls.unit else None,
                    row.get(cls.within) if cls.within else None,
                    onset.rows if onset else 0,
                    onset.last if onset else row["poll"],
                )
                found[key] = onset = first
            onset.rows += 1
            onset.last = max(onset.last, row["poll"])
    return sorted(found.values(), key=lambda o: (o.poll, o.order, o.id))


def _link(onset: Onset, episodes: Sequence[Episode], spec: Map) -> tuple[Episode, str] | None:
    """The episode an onset joins and the link, nesting before thread before coincide; the latest active one wins."""
    near = [e for e in episodes if onset.poll - e.last <= spec.gap]
    tests = (
        (
            "nesting",
            lambda e: (
                (onset.within is not None and (onset.thread, onset.within) in e.units)
                or (onset.unit is not None and (onset.thread, onset.unit) in e.withins)
            ),
        ),
        ("thread", lambda e: onset.thread is not None and onset.thread in e.threads),
    )
    for name, test in tests:
        hits = [e for e in near if test(e)]
        if hits:
            return max(hits, key=lambda e: e.last), name
    hits = [
        e for e in episodes if abs(onset.poll - e.root.poll) <= spec.coincide and onset.unresolved == e.root.unresolved
    ]
    return (max(hits, key=lambda e: (e.root.poll, -e.root.order)), "coincide") if hits else None


def episodes(found: Iterable[Onset], spec: Map) -> list[Episode]:
    out: list[Episode] = []
    for onset in found:
        joined = _link(onset, out, spec)
        if joined:
            joined[0].members.append((onset, joined[1]))
            joined[0].take(onset)
            continue
        episode = Episode(onset, last=onset.poll)
        episode.take(onset)
        out.append(episode)
    return out


def _status(episode: Episode, every: Sequence[Episode], spec: Map) -> tuple[str, list[str], list[str]]:
    poll = episode.root.poll
    active = [e for e in every if e is not episode and e.root.poll < poll <= e.last + spec.gap]
    upstream = [e.root.id for e in active if not e.root.unresolved]
    unknown = [e.root.id for e in active if e.root.unresolved]
    if episode.root.unresolved:
        return "unresolved", upstream, unknown
    return ("provisional" if upstream else "root"), upstream, unknown


def _covered(report: Mapping[str, Any], spec: Map) -> int | None:
    value: Any = report
    for part in spec.covered:
        value = value.get(part) if isinstance(value, Mapping) else None
    return value if spec.covered and type(value) is int else None


def census(report: Mapping[str, Any], spec: Map, members: int | None = None) -> dict[str, Any]:
    """The report reduced to episodes, roots first in poll order; `members` caps each episode's consequence list."""
    found = onsets(report, spec)
    every = episodes(found, spec)
    out, status = [], Counter()
    for episode in every:
        state, upstream, unknown = _status(episode, every, spec)
        status[state] += 1
        rows = Counter({episode.root.cls: episode.root.rows})
        for onset, _ in episode.members:
            rows[onset.cls] += onset.rows
        listed = episode.members if members is None else episode.members[:members]
        out.append(
            {
                "id": episode.root.id,
                "status": state,
                "class": episode.root.cls,
                "poll": episode.root.poll,
                "through": episode.root.poll + spec.margin,
                "locus": list(episode.root.locus),
                "row": episode.root.row,
                "rows": dict(sorted(rows.items())),
                "onsets": 1 + len(episode.members),
                "last": max(episode.last, episode.root.last),
                "links": dict(sorted(Counter(link for _, link in episode.members).items())),
                "upstream": upstream,
                "unresolved_upstream": unknown,
                "members": [
                    {"id": o.id, "class": o.cls, "locus": list(o.locus), "poll": o.poll, "link": link}
                    for o, link in listed
                ],
            }
        )
    rows = _rows(report)
    return {
        "covered": _covered(report, spec),
        "rows": {c.name: len(rows.get(c.name, [])) for c in spec.classes},
        "onsets": len(found),
        "episodes": len(out),
        "status": {name: status[name] for name in ("root", "provisional", "unresolved")},
        "dump_polls": sorted({e["poll"] for e in out if e["status"] == "unresolved"}),
        "roots": out,
    }


def _keys(kept: Mapping[str, Any]) -> set[tuple[Any, ...]]:
    keys = set()
    for episode in kept.get("roots", []):
        keys.add((episode["class"], *episode["locus"]))
        keys.update((m["class"], *m["locus"]) for m in episode.get("members", []))
    return keys


def verify(report: Mapping[str, Any], spec: Map, kept: Mapping[str, Any], names: Sequence[str] = ()) -> dict[str, Any]:
    """Each kept root (or each named one) against this report: `gone`, `present` (same locus, same poll), `moved`
    (same locus, another poll) or `beyond` (past the polls this report compared); `new` lists this report's roots
    whose locus the kept census never had. Names that the kept census does not hold are refused."""
    if not isinstance(kept.get("roots"), list) or "members" not in (kept["roots"] or [{"members": []}])[0]:
        raise TasteError("lockstep.against", "the kept census holds no roots with members (write it with --out)")
    now = census(report, spec)
    first = {}
    for onset in onsets(report, spec):
        first.setdefault(onset.key, onset.poll)
    chosen = kept["roots"]
    if names:
        by = {e["id"]: e for e in chosen}
        if unknown := [n for n in names if n not in by]:
            raise TasteError("lockstep.root", f"the kept census has no root {', '.join(unknown)}")
        chosen = [by[n] for n in names]
    covered = now["covered"]
    states = []
    for episode in chosen:
        key = (episode["class"], *episode["locus"])
        if covered is not None and episode["poll"] > covered:
            state, poll = "beyond", None
        elif key in first:
            poll = first[key]
            state = "present" if poll == episode["poll"] else "moved"
        else:
            state, poll = "gone", None
        states.append({"id": episode["id"], "state": state, "poll": poll, "through": episode["through"]})
    known = _keys(kept)
    new = [
        {"id": e["id"], "status": e["status"], "poll": e["poll"]}
        for e in now["roots"]
        if (e["class"], *e["locus"]) not in known
    ]
    return {"covered": covered, "roots": states, "new": new, "census": now}
