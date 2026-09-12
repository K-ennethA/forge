"""Floor plans (Phase 19) — the plan contract, imported straight out of ``service/``.

``maker.py``'s coupling, for ``maker.py``'s reasons, and they hold harder here:

* **There is no endpoint to call.** ``service/floorplan.py`` ships as plain
  Python and the service exposes no ``/floorplan`` route (docs/architecture.md
  says so out loud). Adding one would be a change to ``service/``, which this
  round does not touch.
* **It is arithmetic and a schema, not state.** ``validate_plan``,
  ``fill_defaults``, ``diff_plans`` and the appliance table are pure functions
  over a dict — no session, no file, no job. Two processes resolving the same
  plan is not a consistency problem.
* **Reimplementing it would be a lie.** The resolved plan is exactly what the
  add-on's ``build_floorplan`` eats, and ``DEFAULTS`` is its ``PLAN_DEFAULTS``
  key for key. A second copy of the defaults in this file would be a level built
  at a ceiling height nobody typed.

The one cost is numpy: ``service/floorplan.py`` imports it at module level for
``plan_mask``. The MCP venv has it (``service_client`` does not need it, but the
mask half is not reached from here anyway), and if it is missing the import
failure is one plain sentence naming what to do, exactly like maker mode's —
lazy and guarded, so every other tool in the server carries on working.

``service/floorplan_extract.py`` rides in on the same argument, and it brings
**Pillow** with it the way ``floorplan.py`` brought numpy — stated in
``mcp/pyproject.toml`` rather than hidden, imported lazily inside the extractor
so a venv without it still validates, diffs and builds plans. It is here for the
one reason the whole module exists: a drawing that got read by EYE produced a
level with the wrong footprint and a diagonal wall that exists nowhere in it, so
the reading has to be arithmetic, and arithmetic the model cannot reach around
has to be a tool.

**What is NOT mirrored here:** ``plan_mask`` / ``mask_iou`` / ``plan_bounds``
(the verification tier — that is a render-vs-mask comparison and it belongs with
``verify_design``), ``snap_segments`` (the line-art half of extraction, which
has no caller until a drawing arrives as strokes rather than as blocks) and
``component_build_specs`` (the add-on computes its own from the resolved plan;
sending specs as well would be two sources of truth for one wall).
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import config
from .errors import ForgeError

#: The one sentence a caller sees when ``service/`` cannot be imported.
_UNAVAILABLE = (
    "The floor-plan schema could not be loaded from {root}. The MCP server "
    "reads service/floorplan.py and service/appliance_dims.py directly off disk "
    "(there is no HTTP route for them — they are the plan contract, not a "
    "geometry job). Check that the repo's service/ folder is beside mcp/, or set "
    "FORGE_SERVICE_PACKAGE_ROOT to the folder that contains it. The underlying "
    "error was: {detail}"
)

#: Cached ``(floorplan, appliance_dims)`` once the import has succeeded.
_MODULES: Optional[Tuple[Any, Any]] = None

#: Cached ``service.floorplan_extract``. Separate from the pair above because it
#: is the one piece of this with a dependency that can be missing on its own:
#: Pillow. A server without it still validates, diffs and builds plans.
_EXTRACTOR: Optional[Any] = None


def modules() -> Tuple[Any, Any]:
    """``(service.floorplan, service.appliance_dims)``, imported on first use."""
    global _MODULES
    if _MODULES is not None:
        return _MODULES

    root = config.SERVICE_PACKAGE_ROOT
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from service import appliance_dims as _appliance  # noqa: PLC0415
        from service import floorplan as _floorplan  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — any import failure is one answer
        raise ForgeError(_UNAVAILABLE.format(root=root, detail=exc)) from exc

    _MODULES = (_floorplan, _appliance)
    return _MODULES


def extractor() -> Any:
    """``service.floorplan_extract``, imported on first use."""
    global _EXTRACTOR
    if _EXTRACTOR is not None:
        return _EXTRACTOR
    modules()  # same sys.path work, same one sentence if the repo is not there
    try:
        from service import floorplan_extract as _extract  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        raise ForgeError(
            _UNAVAILABLE.format(root=config.SERVICE_PACKAGE_ROOT, detail=exc)
        ) from exc
    _EXTRACTOR = _extract
    return _EXTRACTOR


def _service_errors() -> tuple:
    """The service's own exception base, for translating into a ToolError."""
    try:
        from service.errors import ForgeError as ServiceForgeError  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return ()
    return (ServiceForgeError,)


def _translate(call, *args, **kwargs):
    """Run a service call, passing its refusals through VERBATIM.

    ``FloorPlanError``'s messages name the entry they are about — ``"room
    'room-bowtie' crosses itself: the wall from corner 0 to 1 meets the wall
    from corner 2 to 3"`` — which is the id the artist and the model both share.
    Rewriting one would lose the only word that locates the problem on the
    drawing, so the sentence crosses into the tool result unchanged.
    """
    known = _service_errors()
    try:
        return call(*args, **kwargs)
    except known as exc:  # type: ignore[misc]
        raise ForgeError(str(exc)) from exc


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


def defaults() -> Dict[str, Any]:
    """``service.floorplan.DEFAULTS`` — the add-on's ``PLAN_DEFAULTS``, mirrored."""
    plan_module, _appliance = modules()
    return dict(plan_module.DEFAULTS)


def plan_version() -> int:
    plan_module, _appliance = modules()
    return int(plan_module.PLAN_VERSION)


def validate(plan: Any) -> Dict[str, Any]:
    """The plan, checked and normalised — ids stripped, numbers floats."""
    plan_module, _appliance = modules()
    return _translate(plan_module.validate_plan, plan)


def resolve(plan: Any) -> Dict[str, Any]:
    """The plan with every optional number filled in, plus ``provenance``.

    This is what ``build_floorplan`` consumes, so it is what crosses the socket:
    a plan resolved HERE is a plan the add-on cannot silently substitute its own
    defaults into.
    """
    plan_module, _appliance = modules()
    return _translate(plan_module.fill_defaults, plan)


def diff(old: Any, new: Any) -> Dict[str, Any]:
    """``diff_plans`` plus the kind of each id, so a report can count nouns.

    The service answers in ids alone, which is right for it — an id is what the
    add-on builds by. But "this edit rebuilds 2 walls and adds 1 fixture" is the
    sentence the artist needs before they say yes, and that needs to know what
    each id *is*. The kinds come from the two resolved plans (new first, because
    a changed entry's current kind is the one being built), never invented.
    """
    plan_module, _appliance = modules()
    summary = _translate(plan_module.diff_plans, old, new)

    kinds: Dict[str, str] = {}
    kinds.update(entry_kinds(resolve(old)))
    kinds.update(entry_kinds(resolve(new)))  # new wins where both name an id

    out: Dict[str, Any] = {key: list(value) for key, value in summary.items()}
    out["kinds"] = kinds
    return out


def entry_kinds(resolved: Mapping[str, Any]) -> Dict[str, str]:
    """``{id: "room"|"wall"|"opening"|"label"}`` for one resolved plan."""
    kinds: Dict[str, str] = {}
    for room in resolved.get("rooms") or []:
        if isinstance(room, Mapping) and room.get("id"):
            kinds[str(room["id"])] = "room"
    for wall in resolved.get("walls") or []:
        if not isinstance(wall, Mapping) or not wall.get("id"):
            continue
        kinds[str(wall["id"])] = "wall"
        for opening in wall.get("openings") or []:
            if isinstance(opening, Mapping) and opening.get("id"):
                kinds[str(opening["id"])] = "opening"
    for label in resolved.get("labels") or []:
        if isinstance(label, Mapping) and label.get("id"):
            kinds[str(label["id"])] = "label"
    return kinds


def counts(resolved: Mapping[str, Any]) -> Dict[str, int]:
    """How many of each kind are in a resolved plan."""
    tally = {"room": 0, "wall": 0, "opening": 0, "label": 0}
    for kind in entry_kinds(resolved).values():
        tally[kind] = tally.get(kind, 0) + 1
    return tally


def appliance_matches(resolved: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """One record per fixture: what the label was read as, and how sure that is.

    A fixture with no match is still listed, with ``match: None``, because that
    is a real answer and the artist has to see it: the size then comes from the
    footprint THEY drew, which is exactly right for a sofa somebody's
    grandmother made and exactly wrong to leave unsaid.
    """
    _plan_module, appliance = modules()
    provenance = resolved.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}

    out: List[Dict[str, Any]] = []
    for label in resolved.get("labels") or []:
        if not isinstance(label, Mapping) or not label.get("id"):
            continue
        text = str(label.get("label") or "")
        found = appliance.lookup(text) if text else None
        record = provenance.get(str(label["id"]))
        record = record if isinstance(record, Mapping) else {}
        out.append({
            "id": str(label["id"]),
            "label": text,
            "match": found["match"] if found else None,
            "how": found["how"] if found else None,
            "confidence": found["confidence"] if found else None,
            "height_mm": label.get("height_mm"),
            "height_from": record.get("height_from"),
            "note": found["note"] if found else None,
        })
    return out


def from_defaults(resolved: Mapping[str, Any]) -> List[Tuple[str, List[str]]]:
    """``[(id, [field, ...]), ...]`` for every entry that took a default.

    Reads the top-level ``provenance`` map, which is where the service keeps it
    precisely so the entries stay pure resolved schema — the add-on fingerprints
    each entry's canonical JSON, and a note about where a number came from would
    make a wall look changed when only the bookkeeping moved.
    """
    provenance = resolved.get("provenance")
    if not isinstance(provenance, Mapping):
        return []
    out: List[Tuple[str, List[str]]] = []
    for ident, record in provenance.items():
        if not isinstance(record, Mapping):
            continue
        fields = [str(field) for field in (record.get("from_defaults") or [])]
        if fields:
            out.append((str(ident), fields))
    out.sort(key=lambda item: item[0])
    return out


# ---------------------------------------------------------------------------
# The drawing (Phase 19's extraction half)
# ---------------------------------------------------------------------------


def extract(image_path: Any, legend: Any = None, mm_per_px: Any = None,
            grid_px: Any = None) -> Dict[str, Any]:
    """Measure a drawing into a plan — ``{"plan", "regions", "mask", "report"}``.

    Refusals cross verbatim like every other one in this module; the extractor's
    name the file and say what it saw in it.
    """
    module = extractor()
    return _translate(module.extract_floorplan, image_path, legend=legend,
                      mm_per_px=mm_per_px, grid_px=grid_px)


#: Regions listed by name in an extract report before it starts counting. Each
#: one is a crop the model has to LOOK at, so a list too long to act on is a
#: list that turns into "and some other rooms".
EXTRACT_REGIONS_LISTED = 12

#: The law the report exists to enforce, said in the report rather than left to
#: the prompt — the same arrangement as `FLOORPLAN_GATE`, and for a harder
#: reason: the failure that produced this tool was a model reading a picture.
EXTRACT_LAW = (
    "GEOMETRY CAME FROM THE PIXELS, NOT FROM LOOKING. Do not adjust a "
    "coordinate here by eye and do not re-type the plan — if something is "
    "wrong, say which region or wall and re-extract with a legend, a grid_px "
    "or a scale. What you DO supply is the names: read each crop and set that "
    "region's label."
)


def fmt_extract_report(result: Mapping[str, Any], *, image: Any,
                       saved: Any = None, overwritten: bool = False,
                       slug: Optional[str] = None) -> str:
    """The reading, in words the artist can correct before anything is built.

    Written to be QUOTED, like every other report in this group. The order is
    the order the next few turns happen in: what was measured, what has to be
    named, the one question, then the gate.
    """
    report = result.get("report") or {}
    regions = result.get("regions") or []
    counts_ = report.get("counts") or {}
    plan = result.get("plan") or {}

    lines = [
        f"Read {image} — {counts_.get('rooms', 0)} region(s), "
        f"{counts_.get('walls', 0)} wall(s) "
        f"({counts_.get('walls_shared', 0)} shared, "
        f"{counts_.get('walls_exterior', 0)} outside), "
        f"{counts_.get('openings', 0)} opening(s). Measured, not eyeballed."
    ]
    grid = report.get("grid") or {}
    if grid.get("pitch_px"):
        lines.append(
            f"  grid: {grid.get('source')} pitch {grid['pitch_px']:g} px, worst "
            f"snap {grid.get('worst_snap_px')} px (never more than "
            f"{grid.get('tolerance_px')} px, whatever the pitch)"
        )
    else:
        lines.append("  grid: none fitted, so coordinates are where they were measured")
    if report.get("wall_gap_px"):
        lines.append(
            f"  wall thickness read off the drawing: {report['wall_gap_px']:g} px "
            f"(anything wider than {report.get('wall_gap_limit_px')} px was taken "
            f"for open space, not a wall)"
        )
    fidelity = (report.get("fidelity") or {}).get("rooms_vs_fill_iou")
    if fidelity is not None:
        lines.append(
            f"  the rooms cover {float(fidelity) * 100:.1f}% of the colour that was "
            f"actually filled in (1.0 is a pixel-perfect reading)"
        )

    lines.append("  NAME THESE — look at each crop and give it a label:")
    for region in regions[:EXTRACT_REGIONS_LISTED]:
        box = (region.get("crop") or {}).get("box_px") or region.get("crop_px")
        size = region.get("size_mm") or []
        lines.append(
            f"    {region.get('id')}: crop {box}, "
            + (f"{size[0]:g} x {size[1]:g} mm, " if len(size) == 2 else "")
            + f"{region.get('corners')} corners, confidence "
            + f"{region.get('confidence')}"
        )
    if len(regions) > EXTRACT_REGIONS_LISTED:
        lines.append(f"    ... and {len(regions) - EXTRACT_REGIONS_LISTED} more")

    legend_block = report.get("legend") or {}
    for sentence in (legend_block.get("assumed") or []):
        lines.append(f"  ASSUMED: {sentence}")
    if legend_block.get("given"):
        lines.append(
            "  colour key you gave: "
            + ", ".join(f"{item['hex']} = {item['role']}"
                        for item in legend_block["given"])
        )
    for opening in (report.get("openings") or [])[:EXTRACT_REGIONS_LISTED]:
        lines.append(
            f"  {opening.get('id')}: {opening.get('kind')} "
            f"({opening.get('role')}, {opening.get('color')}) on "
            f"{opening.get('wall')}, {opening.get('width_mm'):g} mm wide at "
            f"{opening.get('at_mm'):g} mm along it"
        )

    for sentence in (report.get("ambiguous") or []):
        lines.append(f"  AMBIGUOUS: {sentence}")
    for sentence in (report.get("warnings") or []):
        lines.append(f"  WARNING: {sentence}")
    for sentence in (report.get("notes") or []):
        lines.append(f"  note: {sentence}")

    if saved is not None:
        lines.append(
            f"  {'updated' if overwritten else 'saved'} the plan — {saved}"
        )
    elif slug is None:
        lines.append(
            "  nothing was saved: pass `project` to file this under "
            "projects/<slug>/design/floorplan.json"
        )

    question = report.get("calibration_question")
    if question:
        lines.append(f"  ASK THIS, and nothing else: {question}")
        hints = report.get("calibration_hints") or []
        if hints:
            lines.append(
                "  then mm_per_px = their millimetres / one of these pixel "
                "lengths: "
                + "; ".join(f"{hint['what']} = {hint['length_px']:g} px"
                            for hint in hints[:4])
            )
        lines.append(
            "  re-run floorplan_extract with that mm_per_px — do not scale the "
            "numbers by hand."
        )
    else:
        scale = (report.get("scale") or {}).get("mm_per_px")
        lines.append(f"  scale: {scale} mm per pixel, as you gave it")

    lines.append(f"  {EXTRACT_LAW}")
    lines.append(
        "  next: name the regions, then render design/floorplan.svg FROM this "
        "plan (never from the picture), name its path, and build only after "
        "they say yes."
    )
    lines.append("  " + str(report.get("honesty") or ""))
    if plan.get("walls"):
        lines.append(
            f"  every one of the {len(plan['walls'])} walls is axis-aligned by "
            f"construction: this reader has no code path that can tilt one."
        )
    return "\n".join(lines)



# ---------------------------------------------------------------------------
# Reconcile — the artist's hand edits, measured and folded back in
# ---------------------------------------------------------------------------
#
# "I should be able to manually edit and forge should be aware of my changes",
# and then, after a wall they had deliberately deleted came back twice: "the
# plan should auto update based off my changes, i deleted the wall because there
# isnt a wall there, i'd like to play with things to determine optimal layout,
# having it undone doesnt make sense."
#
# The add-on measures, `service.absorb_reconcile` decides, and this half turns
# the two into sentences. Nothing here has an opinion of its own.


def reconcile_params(resolved: Mapping[str, Any], collection: Any,
                     floor: Any = True) -> Dict[str, Any]:
    """What crosses the socket for ``reconcile_floorplan``.

    The RESOLVED plan, like ``build_floorplan``'s — the add-on compares each
    object's fingerprint against the entry it was built from, and a plan with a
    default left open would fingerprint differently on this side than it did on
    that one, which would make every wall in the level read as stale.
    """
    name = str(collection or "").strip() or "Floorplan"
    return {"plan": dict(resolved), "collection": name, "floor": bool(floor)}


def absorb(plan: Any, report: Any, *, confirm_deletions: bool = False,
           skip_ids: Any = ()) -> Dict[str, Any]:
    """``service.absorb_reconcile`` — the measurement, applied to the plan."""
    plan_module, _appliance = modules()
    return _translate(plan_module.absorb_reconcile, plan, report,
                      confirm_deletions=confirm_deletions,
                      skip_ids=tuple(skip_ids or ()))


def touched_ids(old: Any, new: Any) -> List[str]:
    """Every id the caller's own plan edit touches, added/changed/removed alike.

    These are the ids the scene must NOT be allowed to overrule when a build
    absorbs first and edits second: the plan edit is the later intent, and
    absorbing both would be two answers to one question.
    """
    summary = diff(old, new)
    out: set = set()
    for key in ("added", "changed", "removed"):
        out.update(str(ident) for ident in (summary.get(key) or []))
    return sorted(out)


#: Items named in full in a reconcile report before it starts counting. A report
#: the model cannot quote item by item is a report that becomes "some walls".
RECONCILE_ITEMS_LISTED = 12


def _g(value: Any) -> str:
    """One number, the way a millimetre is written in a sentence."""
    try:
        return "%g" % (float(value) + 0.0)
    except (TypeError, ValueError):
        return str(value)


def _point(value: Any) -> str:
    if isinstance(value, Mapping):
        value = value.get("size") or []
    if isinstance(value, (list, tuple)):
        return "(" + ", ".join(_g(item) for item in value) + ")"
    return _g(value)


def _field_line(field: str, was: Any, now: Any) -> str:
    return f"{field} {_point(was)} -> {_point(now)}"


def _measurement_line(record: Mapping[str, Any], verb: str) -> str:
    kind = str(record.get("kind") or "entry")
    noun = "fixture" if kind == "label" else kind
    measured = record.get("measured") or {}
    was = record.get("was") or {}
    changes = [
        _field_line(field, was.get(field), measured.get(field))
        for field in (record.get("changed") or [])
    ]
    distance = record.get("moved_mm")
    moved = f" by {_g(distance)} mm" if distance else ""
    mesh = record.get("mesh")
    note = " (its mesh was edited too, and is still a box)" \
        if mesh == "edited-but-still-a-box" else ""
    return (f"  {verb} {record.get('id')} ({noun}){moved} — "
            + "; ".join(changes) + note)


def fmt_reconcile_report(
    result: Mapping[str, Any],
    *,
    source: str,
    absorbed: Optional[Mapping[str, Any]] = None,
    saved: Any = None,
    overwritten: bool = False,
    diff_quote: Optional[str] = None,
) -> str:
    """What the artist changed with their hands, in millimetres and in words.

    Written to be QUOTED. The order is the order the next turn happens in: what
    was measured, what was absorbed, what needs a word from them, and then the
    law — the plan learns from the scene, and the scene is never corrected back.
    """
    collection = result.get("collection") or "Floorplan"
    clean = [str(i) for i in (result.get("clean") or [])]
    stale = [str(i) for i in (result.get("stale") or [])]
    moved = [r for r in (result.get("moved") or []) if isinstance(r, Mapping)]
    resized = [r for r in (result.get("resized") or []) if isinstance(r, Mapping)]
    deleted = [r for r in (result.get("deleted_in_scene") or [])
               if isinstance(r, Mapping)]
    candidates = [r for r in (result.get("candidates") or []) if isinstance(r, Mapping)]
    blocked = [r for r in (result.get("unabsorbable") or []) if isinstance(r, Mapping)]

    seconds = result.get("seconds")
    lines = [
        f"Measured '{collection}' against {source} — "
        f"{result.get('objects', 0)} FP: object(s), "
        f"{result.get('plan_entries', 0)} plan entr(ies)"
        + (f", {_g(seconds)} s." if seconds is not None else "."),
        f"  clean {len(clean)}   moved {len(moved)}   resized {len(resized)}   "
        f"deleted in the scene {len(deleted)}   stale {len(stale)}   "
        f"new boxes {len(candidates)}   cannot absorb {len(blocked)}",
    ]

    for record in moved[:RECONCILE_ITEMS_LISTED]:
        lines.append(_measurement_line(record, "MOVED"))
    for record in resized[:RECONCILE_ITEMS_LISTED]:
        lines.append(_measurement_line(record, "RESIZED"))
    for record in deleted[:RECONCILE_ITEMS_LISTED]:
        kind = str(record.get("kind") or "entry")
        lines.append(
            f"  DELETED IN THE SCENE — {record.get('id')} ({kind}): the plan has "
            f"it and '{collection}' does not."
        )
    for record in candidates[:RECONCILE_ITEMS_LISTED]:
        size = (record.get("bbox_mm") or {}).get("size") or []
        suggested = (record.get("suggested") or {}).get("footprint_mm") or []
        lines.append(
            f"  NEW BOX — {record.get('object')}: "
            + (f"{_g(size[0])} x {_g(size[1])} x {_g(size[2])} mm " if len(size) == 3 else "")
            + (f"at ({_g(suggested[0])}, {_g(suggested[1])}) " if len(suggested) == 4 else "")
            + "— Forge did not build it, so it has no entry. Give it an id and a "
              "label and I will add it; nothing was added on its own."
        )
    for record in blocked[:RECONCILE_ITEMS_LISTED]:
        lines.append(f"  CANNOT ABSORB — {record.get('id')}: {record.get('why')}")
    for key, items in (("moved", moved), ("resized", resized),
                       ("deleted", deleted), ("new boxes", candidates),
                       ("unabsorbable", blocked)):
        if len(items) > RECONCILE_ITEMS_LISTED:
            lines.append(f"    ... and {len(items) - RECONCILE_ITEMS_LISTED} "
                         f"more {key}")

    if absorbed is None:
        pending = len(moved) + len(resized) + len(deleted)
        if pending:
            lines.append(
                f"  NOTHING WAS CHANGED. {pending} measurement(s) are ready to "
                f"go into the plan — run this again with apply=true, or say what "
                f"to leave out."
            )
        else:
            lines.append("  nothing to absorb: the plan already says what the "
                         "scene shows.")
    else:
        applied = [r for r in (absorbed.get("applied") or []) if isinstance(r, Mapping)]
        skipped = [r for r in (absorbed.get("skipped") or []) if isinstance(r, Mapping)]
        gone = [str(i) for i in (absorbed.get("deleted") or [])]
        lines.append(f"  ABSORBED INTO THE PLAN ({len(applied)}):")
        for record in applied[:RECONCILE_ITEMS_LISTED]:
            lines.append(f"    {record.get('what')}")
        if len(applied) > RECONCILE_ITEMS_LISTED:
            lines.append(f"    ... and {len(applied) - RECONCILE_ITEMS_LISTED} more")
        if gone:
            lines.append(
                f"    deletions absorbed, not queried: {', '.join(gone)}. You "
                f"deleted them because they are not there; the plan agrees now."
            )
        for record in skipped[:RECONCILE_ITEMS_LISTED]:
            lines.append(f"    not absorbed — {record.get('why')}")
        if len(skipped) > RECONCILE_ITEMS_LISTED:
            lines.append(f"    ... and {len(skipped) - RECONCILE_ITEMS_LISTED} "
                         f"more left alone")
        if saved is not None:
            lines.append(f"  {'updated' if overwritten else 'saved'} the plan — "
                         f"{saved}")
        if diff_quote:
            lines.append(diff_quote)
        lines.append(
            "  NOTHING WAS REBUILT and nothing needs to be: the plan now says "
            "what the scene already shows, so the next build finds those ids "
            "unchanged and leaves them alone."
        )

    for sentence in (result.get("notes") or []):
        lines.append(f"  note: {sentence}")
    for sentence in (result.get("warnings") or []):
        lines.append(f"  WARNING: {sentence}")
    lines.append(f"  {RECONCILE_LAW}")
    lines.append("  " + str(result.get("honesty") or ""))
    return "\n".join(lines)


#: The law this whole round exists to enforce, carried in the report rather than
#: left to the prompt — `EXTRACT_LAW`'s arrangement, for the same reason: the
#: failure that produced this tool was Forge putting a deleted wall back.
RECONCILE_LAW = (
    "THE PLAN LEARNS FROM THE SCENE, NEVER THE OTHER WAY ROUND. Never rebuild "
    "to 'fix' something they moved and never re-add something they deleted — "
    "absorb it, say what you absorbed, and let them keep playing with the "
    "layout."
)


def fmt_sync_lines(result: Mapping[str, Any],
                   absorbed: Mapping[str, Any],
                   saved: Any = None) -> List[str]:
    """The two or three lines a BUILD leads with when it absorbed first.

    Short on purpose: the build report is the thing being read, and this is the
    sentence that has to come before it — what the artist did with their hands,
    which the build is about to treat as the plan's own.
    """
    applied = [r for r in (absorbed.get("applied") or []) if isinstance(r, Mapping)]
    if not applied:
        return []
    gone = [str(i) for i in (absorbed.get("deleted") or [])]
    lines = [
        f"Absorbed your scene edits into the plan first ({len(applied)}), "
        f"before building — nothing you moved or deleted was put back:"
    ]
    for record in applied[:RECONCILE_ITEMS_LISTED]:
        lines.append(f"  {record.get('what')}")
    if len(applied) > RECONCILE_ITEMS_LISTED:
        lines.append(f"  ... and {len(applied) - RECONCILE_ITEMS_LISTED} more")
    if gone:
        lines.append(f"  the plan no longer has {', '.join(gone)} in it at all.")
    if saved is not None:
        lines.append(f"  the plan on disk is up to date — {saved}")
    return lines


__all__ = [
    "EXTRACT_LAW",
    "RECONCILE_LAW",
    "absorb",
    "appliance_matches",
    "counts",
    "defaults",
    "diff",
    "entry_kinds",
    "extract",
    "extractor",
    "fmt_extract_report",
    "fmt_reconcile_report",
    "fmt_sync_lines",
    "from_defaults",
    "modules",
    "plan_version",
    "reconcile_params",
    "resolve",
    "touched_ids",
    "validate",
]
