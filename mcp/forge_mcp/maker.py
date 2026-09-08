"""Maker mode's data and arithmetic — imported straight out of ``service/``.

THE COUPLING, SAID OUT LOUD
===========================
Every other backend in this server is reached over a wire: the Blender add-on on
a socket, the geometry service and meshgen over HTTP. **This one is not.**
``service/components.py``, ``service/wiring.py`` and the ``*_plan()`` half of
``service/maker_lib.py`` are imported into this process and called directly.

Why that is the right call here, and not a shortcut:

* **There is no endpoint to call.** The maker modules ship as plain Python. The
  service exposes ``/generate``, ``/check``, ``/segment`` and friends; it does
  not expose ``/circuit_plan``. Adding one would be a change to ``service/``,
  which this round does not touch.
* **They are dependency-free.** ``service/__init__.py`` imports nothing,
  ``errors`` and ``printer`` are stdlib, ``components`` is a data table, and
  ``wiring`` is ``math``. ``forge_lib`` and ``maker_lib`` import build123d
  **lazily, inside the geometry functions**, so the arithmetic half loads
  without the kernel. Nothing here pulls a wheel into the MCP venv, and the MCP
  venv does not have to become the service venv.
* **It is arithmetic, not state.** Ohm's law over a table of datasheet numbers.
  There is no session, no file, no job, nothing to be out of date with. Two
  processes computing the same resistor is not a consistency problem.
* **Reimplementing it would be a lie.** A second copy of the resistor maths in
  this file would drift from the one the part scripts actually run, and then the
  number in the chat would stop matching the number in the model.

What that coupling costs, and the guard on it
---------------------------------------------
This server now knows where ``service/`` is on disk. If the repo is split, or a
future edit gives ``forge_lib`` or ``printer`` a top-level build123d import, the
import fails — so it is **lazy and guarded**: nothing loads at server start, the
failure is one plain sentence naming what to do, and every other tool in the
server carries on working. ``FORGE_SERVICE_PACKAGE_ROOT`` moves the search.

What is NOT available here, and why
-----------------------------------
Everything that builds a **solid**: ``envelope``, ``cutout``, ``mount``,
``plunger``, ``snap_clip``, ``battery_door``, ``plunger_cap_socket``. Those call
build123d, which lives in the service's venv, and geometry belongs on the
service side anyway. Maker geometry reaches the artist the way all PartForge
geometry does — a script written with ``partforge_new_part`` that composes
``maker_lib`` and is built by ``partforge_generate``. The tools here are the
half that has to happen **before** the script exists (which parts to buy) and
**after** it is printed (what to solder).
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import config
from .errors import ForgeError

#: The one sentence a caller sees when ``service/`` cannot be imported.
_UNAVAILABLE = (
    "Maker mode's component table could not be loaded from {root}. The MCP "
    "server reads service/components.py, service/wiring.py and the arithmetic "
    "half of service/maker_lib.py directly off disk (they are dependency-free "
    "by design). Check that the repo's service/ folder is beside mcp/, or set "
    "FORGE_SERVICE_PACKAGE_ROOT to the folder that contains it. The underlying "
    "error was: {detail}"
)

#: Cached ``(components, wiring, maker_lib)`` once the import has succeeded.
_MODULES: Optional[Tuple[Any, Any, Any]] = None


def modules() -> Tuple[Any, Any, Any]:
    """``(components, wiring, maker_lib)``, imported on first use and cached.

    Deliberately lazy: a checkout without ``service/`` still starts a server
    where all 63 other tools work.
    """
    global _MODULES
    if _MODULES is not None:
        return _MODULES

    root = config.SERVICE_PACKAGE_ROOT
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from service import components as _components  # noqa: PLC0415
        from service import maker_lib as _maker_lib  # noqa: PLC0415
        from service import wiring as _wiring  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — any import failure is the same answer
        raise ForgeError(_UNAVAILABLE.format(root=root, detail=exc)) from exc

    _MODULES = (_components, _wiring, _maker_lib)
    return _MODULES


def _service_errors() -> tuple:
    """The service's own exception base, for translating into a ToolError."""
    try:
        from service.errors import ForgeError as ServiceForgeError  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return ()
    return (ServiceForgeError,)


def _translate(call, *args, **kwargs):
    """Run a service call, turning its refusals into ForgeError.

    ``ComponentError``, ``CircuitError`` and ``PrintabilityError`` all derive
    from the service's ``ForgeError`` and all carry a sentence written to be
    read and acted on — exactly the contract ``forge_mcp.errors`` has with the
    model, so they are passed through verbatim rather than rewritten.
    """
    known = _service_errors()
    try:
        return call(*args, **kwargs)
    except known as exc:  # type: ignore[misc]
        raise ForgeError(str(exc)) from exc


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------

#: Order categories are listed in: the things that DO something first, the
#: things that hold them together last.
CATEGORY_ORDER: Tuple[str, ...] = ("switch", "power", "light", "fastener", "magnet")

CATEGORY_BLURB: Dict[str, str] = {
    "switch": "what the finger operates",
    "power": "what feeds it",
    "light": "what lights up",
    "fastener": "what holds the pieces together",
    "magnet": "what holds a door shut",
}


def categories() -> Tuple[str, ...]:
    components, _wiring, _maker = modules()
    known = tuple(components.CATEGORIES)
    ordered = [name for name in CATEGORY_ORDER if name in known]
    ordered.extend(name for name in known if name not in ordered)
    return tuple(ordered)


def catalog(filter_text: Optional[str] = None) -> Dict[str, Any]:
    """Component records matching *filter_text*, plus how they were matched.

    Returns ``{"records": [...], "match": <"all"|"category"|"name"|"search">,
    "query": <str|None>, "categories": (...)}``. A ``name`` match is one exact
    hit and is rendered as the full card; everything else lists.
    """
    components, _wiring, _maker = modules()
    order = categories()
    everything = [components.component(name) for name in components.catalog()]

    query = (filter_text or "").strip()
    if not query:
        return {"records": everything, "match": "all", "query": None,
                "categories": order}

    key = query.lower().replace(" ", "_").replace("-", "_")

    if key in order:
        return {
            "records": [rec for rec in everything if rec["category"] == key],
            "match": "category", "query": key, "categories": order,
        }

    exact = [rec for rec in everything if rec["name"] == key]
    if exact:
        return {"records": exact, "match": "name", "query": key,
                "categories": order}

    hits = [
        rec for rec in everything
        if key in rec["name"]
        or key in str(rec.get("kind", "")).lower()
        or query.lower() in str(rec.get("summary", "")).lower()
    ]
    if not hits:
        raise ForgeError(
            f"Nothing in the maker catalog matches {query!r}. The categories are "
            + ", ".join(order)
            + "; the parts are "
            + ", ".join(rec["name"] for rec in everything)
            + ". Call maker_components() with no filter to see the lot."
        )
    if len(hits) == 1:
        return {"records": hits, "match": "name", "query": key,
                "categories": order}
    return {"records": hits, "match": "search", "query": query,
            "categories": order}


def clone_tolerance_mm() -> float:
    components, _wiring, _maker = modules()
    return float(components.CLONE_TOLERANCE_MM)


def led_colors() -> List[str]:
    components, _wiring, _maker = modules()
    return sorted(components.LED_FORWARD_VOLTAGE)


# ---------------------------------------------------------------------------
# The circuit
# ---------------------------------------------------------------------------


def circuit(
    led: str = "led_5mm",
    *,
    color: Optional[str] = None,
    cell: str = "cr2032_cell",
    cells: int = 1,
    switch: Optional[str] = "tactile_6x6_latching",
    current_ma: Optional[float] = None,
) -> Dict[str, Any]:
    """``wiring.circuit_plan``, with the service's refusals passed through."""
    _components, wiring, _maker = modules()
    return _translate(
        wiring.circuit_plan,
        led,
        color=color,
        cell=cell,
        cells=int(cells),
        switch=switch,
        current_ma=current_ma,
    )


def wiring_guide(
    led: str = "led_5mm",
    *,
    color: Optional[str] = None,
    cell: str = "cr2032_cell",
    cells: int = 1,
    switch: Optional[str] = "tactile_6x6_latching",
    current_ma: Optional[float] = None,
) -> Dict[str, Any]:
    """The plan, the soldering steps and the shopping list, in one dict."""
    _components, wiring, _maker = modules()
    plan = circuit(led, color=color, cell=cell, cells=cells, switch=switch,
                   current_ma=current_ma)
    return {
        "plan": plan,
        "steps": _translate(wiring.wiring_steps, plan),
        "bom": _translate(wiring.bill_of_materials, plan),
    }


# ---------------------------------------------------------------------------
# The push mechanic
# ---------------------------------------------------------------------------


def plunger(
    stem_diameter: float,
    *,
    switch: str = "tactile_6x6_latching",
    guide_length: Optional[float] = None,
    overtravel: Optional[float] = None,
    keyed: bool = True,
) -> Dict[str, Any]:
    """``maker_lib.plunger_plan`` — the kinematics, with no kernel involved."""
    _components, _wiring, maker_lib = modules()
    kwargs: Dict[str, Any] = {"switch": switch, "keyed": bool(keyed)}
    if guide_length is not None:
        kwargs["guide_length"] = float(guide_length)
    if overtravel is not None:
        kwargs["overtravel"] = float(overtravel)
    return _translate(maker_lib.plunger_plan, float(stem_diameter), **kwargs)


__all__ = [
    "CATEGORY_BLURB",
    "CATEGORY_ORDER",
    "catalog",
    "categories",
    "circuit",
    "clone_tolerance_mm",
    "led_colors",
    "modules",
    "plunger",
    "wiring_guide",
]
