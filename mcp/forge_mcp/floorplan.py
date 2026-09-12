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

**What is NOT mirrored here:** ``plan_mask`` / ``mask_iou`` / ``plan_bounds``
(the verification tier — that is a render-vs-mask comparison and it belongs with
``verify_design``), ``snap_segments`` (the deterministic half of extraction,
which has no drawing to run on until there is an image pipeline) and
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


__all__ = [
    "appliance_matches",
    "counts",
    "defaults",
    "diff",
    "entry_kinds",
    "from_defaults",
    "modules",
    "plan_version",
    "resolve",
    "validate",
]
