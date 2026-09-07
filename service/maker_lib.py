"""``maker_lib`` -- real components as design elements, with the mechanisms engineered.

``forge_lib`` makes a shape the printer can hold.  ``maker_lib`` makes a shape
the *world* fits into: a switch that a finger can actually operate, an LED that
stays where you pressed it, a cell you can change, and the circuit that joins
them.

The gap this closes
-------------------
Leaving a gap so a part is "removable" is not a mechanism.  A push button is a
mechanism: the plunger's travel has to be at least the switch's actuation
stroke, its guide has to be long enough that the plunger does not cock and jam,
something has to stop it over-compressing the switch, something has to stop it
falling out, and the whole thing has to be assemblable in an order that exists.
Every helper in here computes those numbers from the component's own dimensions
and **reports them**, the same way every ``forge_lib`` helper reports its
clamps.

Three layers
------------
:mod:`service.components`
    The data: real parts, dimensioned, each with a
    ``verify_against_your_part`` sentence because clones vary.
This module
    The geometry: :func:`envelope` (keep-out), :func:`cutout` (the negative,
    with fits derived from ``printer.json``), :func:`mount` (the positive), and
    the mechanisms -- :func:`plunger`, :func:`snap_clip`, :func:`battery_door`.
:mod:`service.wiring`
    The circuit: :func:`circuit_plan` sizes the resistor and
    :func:`wiring_steps` says what to solder, re-exported here so a part script
    needs one import.

Conventions, inherited from ``forge_lib`` unchanged
--------------------------------------------------
* Dimensions are explicit millimetres.
* ``printer`` is an optional partial profile merged over the built-in Centauri
  Carbon default; **every clearance comes from ``printer.tolerances``**, never
  from a number typed into this file.  The two exceptions are named where they
  occur and both are somebody else's standard rather than ours: a heat-set
  insert's hole (the insert maker's number) and an M3 clearance hole (ISO).
* Every helper has a ``*_plan()`` twin returning the numbers it will build
  with, including a ``clamped`` list naming every value the rules moved and why.
* Nothing is silently made sub-minimum: it clamps and says so, or it raises
  :class:`~service.forge_lib.PrintabilityError` with a plain sentence.

Negatives and positives, and which way up
-----------------------------------------
:func:`cutout` follows ``forge_lib.magnet_pocket`` exactly: the negative hangs
**below Z = 0 with its mouth facing up**, poking ``MOUTH_OVERSHOOT_MM`` above
it, so you place it *at the face it is bored into* and it bores straight down::

    body -= Pos(x, y, top_z) * maker_lib.cutout("led_5mm", depth=wall)

:func:`mount` and :func:`envelope` are built in the component's own datum frame
-- each record's ``datum`` sentence says what ``Z = 0`` means for it, and
``plan["datum"]`` repeats it.

Usage from a PartForge script
-----------------------------
``maker_lib`` is installed in a script's namespace beside ``forge_lib``::

    from build123d import *
    import forge_lib, maker_lib

    PARAMS = {"wall": {"value": 2.0, "unit": "mm"}}

    def build(p):
        body = forge_lib.shell_box(40, 40, 30, p["wall"])
        body -= Pos(0, 0, 30) * maker_lib.cutout("led_5mm", depth=p["wall"])
        return body

Authoring guide: ``docs/part-authoring.md``, section 7.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import components as _components
from . import forge_lib as _fl
from .components import ComponentError, catalog, component, led_forward_voltage
from .forge_lib import PrintabilityError, MOUTH_OVERSHOOT_MM
from .printer import normalize_printer
from .wiring import (
    CircuitError,
    bill_of_materials,
    circuit_plan,
    diagram_svg,
    preferred_resistor,
    wiring_steps,
)

# --------------------------------------------------------------------------
# Mechanism constants -- each one is a rule with a reason
# --------------------------------------------------------------------------

#: A sliding pin jams ("cocks") in its bore when the engaged length is short
#: against the diameter: the side load from an off-centre push turns into a
#: couple the bore cannot resist, and the pin wedges diagonally.  Two diameters
#: of engagement is the conservative shop rule and the one this library
#: enforces.  A plunger guide is never shorter than 2 x the stem diameter.
GUIDE_ENGAGEMENT_RATIO = 2.0

#: Extra travel past the switch's actuation point, so tolerance stack-up cannot
#: leave the plunger stopping just short of a click.  Always clamped down to the
#: switch's own ``max_overtravel_mm`` -- a 6x6 tactile's dome bottoms out a
#: quarter of a millimetre past actuation, and driving it further crushes it.
DEFAULT_OVERTRAVEL_MM = 0.5

#: The designed rattle: the gap between the plunger's tip and the switch button
#: when the plunger is lifted against its retention flange.  It exists so that
#: no stack-up of printed tolerances can leave the plunger *preloading* the
#: switch, which is a model that is permanently on.  Gravity closes it, so the
#: plunger's resting place is on the button, not hanging from the flange.
DEFAULT_FREE_PLAY_MM = 0.3

#: A press shorter than this does not read as a press to a fingertip.  Not a
#: printability limit -- a note, because it is the reason a momentary 6x6
#: tactile makes a disappointing figure button.
MIN_SATISFYING_TRAVEL_MM = 1.0

#: How much bore a through-hole LED wants around it to stay pressed in.  Under
#: this the plan says so and points at ``mount(style="collar")``.
LED_GRIP_TARGET_MM = 3.0

#: Cantilever snap-fit strain limits and stiffness for FDM parts.  ``strain`` is
#: the allowable surface strain for a snap-fit arm -- the deflection maths turns
#: on it -- and ``modulus_mpa`` is a printed part's effective Young's modulus,
#: which is well below the injection-moulded figure the datasheet quotes.
#:
#: These are design rules of thumb, not measurements of your spool.  Infill,
#: temperature and above all LAYER DIRECTION move them: a clip printed with its
#: layers running ACROSS the arm snaps at the root on the first flex, because a
#: layer boundary at the point of maximum strain is a crack that has already
#: started.  Print snap arms with the layers running ALONG the arm.
MATERIALS: Dict[str, Dict[str, Any]] = {
    "PLA": {"strain": 0.020, "modulus_mpa": 3500.0, "friction": 0.30,
            "note": "stiff and brittle; the least forgiving snap-fit material here"},
    "PLA-CF": {"strain": 0.010, "modulus_mpa": 5000.0, "friction": 0.30,
               "note": "the fibres make it stiffer and much more brittle -- avoid for clips"},
    "PETG": {"strain": 0.035, "modulus_mpa": 2100.0, "friction": 0.35,
             "note": "the right choice for snap fits: it bends where PLA cracks"},
    "PETG-CF": {"strain": 0.015, "modulus_mpa": 4000.0, "friction": 0.35,
                "note": "stiff; not a snap-fit material"},
    "ABS": {"strain": 0.030, "modulus_mpa": 2200.0, "friction": 0.35,
            "note": "good for clips if you can print it without warping"},
    "ASA": {"strain": 0.030, "modulus_mpa": 2000.0, "friction": 0.35,
            "note": "ABS outdoors; same snap-fit behaviour"},
    "NYLON": {"strain": 0.050, "modulus_mpa": 1500.0, "friction": 0.25,
              "note": "the best snap-fit material and the hardest to print dry"},
    "TPU": {"strain": 0.080, "modulus_mpa": 80.0, "friction": 0.60,
            "note": "flexible; a TPU clip holds nothing but survives anything"},
}

DEFAULT_MATERIAL = "PLA"

#: Deflection multiplier ``K`` in ``y_max = K * strain * L^2 / t`` for a
#: cantilever snap arm, at two documented taper ratios (tip thickness as a
#: fraction of root thickness).  A constant-section beam wastes most of its
#: material -- the strain peaks at the root and falls to nothing at the tip --
#: so tapering it lets the same arm bend half again as far.  Values from the
#: standard snap-fit design charts; anything between is interpolated linearly
#: and the plan says the number it used.
_TAPER_K = {1.0: 0.67, 0.5: 1.09}

#: Below this a snap arm's nib is not a catch, it is a texture.
MIN_NIB_MM = 0.3


# --------------------------------------------------------------------------
# Small shared helpers
# --------------------------------------------------------------------------


def _prof(printer: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    return normalize_printer(printer)


def _tol(name: str, prof: Mapping[str, Any]) -> float:
    return _fl.fit_tolerance(name, prof)


def _positive(value: Any, label: str, allow_zero: bool = False) -> float:
    return _fl._finite_positive(value, label, allow_zero=allow_zero)


def _round(value: float) -> float:
    return round(float(value), 5)


def _attach(shape: Any, name: str, value: Any) -> Any:
    try:
        setattr(shape, name, value)
    except Exception:  # noqa: BLE001 - not every build123d version allows this
        pass
    return shape


def _solid_for(part: Mapping[str, Any], grow: float = 0.0) -> Any:
    """One entry of a component's ``parts`` list as a solid, optionally grown."""
    from build123d import Box, Cylinder, Pos  # noqa: PLC0415

    z0 = float(part["z0_mm"]) - grow
    z1 = float(part["z1_mm"]) + grow
    height = z1 - z0
    if height <= 1e-9:
        return None
    x = float(part.get("x_mm", 0.0))
    y = float(part.get("y_mm", 0.0))
    if part["shape"] == "cylinder":
        return Pos(x, y, z0 + height / 2.0) * Cylinder(
            radius=float(part["diameter_mm"]) / 2.0 + grow, height=height
        )
    return Pos(x, y, z0 + height / 2.0) * Box(
        float(part["length_mm"]) + 2.0 * grow,
        float(part["width_mm"]) + 2.0 * grow,
        height,
    )


def _union(solids: Sequence[Any]) -> Any:
    live = [solid for solid in solids if solid is not None]
    if not live:
        raise PrintabilityError("nothing to build: every piece came out zero-height")
    result = live[0]
    for extra in live[1:]:
        result = result + extra
    return result


# ==========================================================================
# envelope -- the keep-out solid
# ==========================================================================


def envelope_plan(
    name: Any,
    printer: Optional[Mapping[str, Any]] = None,
    *,
    clearance: Optional[float] = None,
    leads: bool = True,
) -> Dict[str, Any]:
    """The keep-out :func:`envelope` will build."""
    entry = _components.component(name)
    prof = _prof(printer)
    gap = (
        _tol("loose_fit", prof)
        if clearance is None
        else _positive(clearance, "clearance", allow_zero=True)
    )
    roles = None if leads else tuple(
        role for role in {part["role"] for part in entry["parts"]} if role != "lead"
    )
    box = _components.extent(entry, roles)
    return {
        "component": entry["name"],
        "category": entry["category"],
        "datum": entry["datum"],
        "clearance_mm": _round(gap),
        "clearance_source": (
            "printer.tolerances.loose_fit" if clearance is None else "caller"
        ),
        "includes_leads": bool(leads),
        "length_mm": _round(box["length_mm"] + 2.0 * gap),
        "width_mm": _round(box["width_mm"] + 2.0 * gap),
        "height_mm": _round(box["height_mm"] + 2.0 * gap),
        "z_min_mm": _round(box["z_min_mm"] - gap),
        "z_max_mm": _round(box["z_max_mm"] + gap),
        "bare": box,
        "honesty": _components.honesty(entry),
        "clamped": [],
    }


def envelope(
    name: Any,
    printer: Optional[Mapping[str, Any]] = None,
    *,
    clearance: Optional[float] = None,
    leads: bool = True,
) -> Any:
    """The space a real component occupies -- a solid to keep clear of.

    Built in the component's own datum frame (``plan["datum"]`` says what
    ``Z = 0`` means for it) and grown all round by ``clearance``, which defaults
    to the profile's ``loose_fit``.

    Use it as a *check*, not as a cutter: subtract it from a draft body and if
    anything disappears, something was in the component's way.  For the negative
    you actually cut, use :func:`cutout`, which applies the right fit face by
    face instead of one blanket gap.
    """
    plan = envelope_plan(name, printer, clearance=clearance, leads=leads)
    entry = _components.component(name)
    parts = [
        part for part in entry["parts"]
        if leads or part["role"] != "lead"
    ]
    solid = _union([_solid_for(part, plan["clearance_mm"]) for part in parts])
    return _attach(solid, "forge_envelope_plan", plan)


# ==========================================================================
# cutout -- the negative, with the fits printer.json says
# ==========================================================================


def _stage_row(stage: Mapping[str, Any], grown: Dict[str, float], prof: Mapping[str, Any],
               z0: float, z1: float) -> Dict[str, Any]:
    fit = stage.get("fit")
    return {
        "note": stage["note"],
        "shape": stage["shape"],
        "fit": fit,
        "fit_mm": _round(_tol(fit, prof)) if fit else 0.0,
        "fit_source": f"printer.tolerances.{fit}" if fit else "no clearance applied",
        "z0_mm": _round(z0),
        "z1_mm": _round(z1),
        **{key: _round(value) for key, value in grown.items()},
    }


def _led_cutout_plan(entry: Mapping[str, Any], prof: Mapping[str, Any],
                     depth: Optional[float], lead_relief: float) -> Dict[str, Any]:
    press = _tol("press_fit", prof)
    slide = _tol("slide_fit", prof)
    loose = _tol("loose_fit", prof)
    wall_floor = float(prof["min_wall_thickness"])

    grip = float(depth) if depth is not None else wall_floor
    grip = _positive(grip, "depth")
    flange_h = float(entry["flange_height_mm"])
    relief = _positive(lead_relief, "lead_relief", allow_zero=True)

    notes: List[str] = []
    if grip < LED_GRIP_TARGET_MM - 1e-9:
        notes.append(
            f"{grip:g} mm of bore is not much to hold a "
            f"{entry['dome_diameter_mm']:g} mm LED -- about {LED_GRIP_TARGET_MM:g} mm "
            f"is what it wants. Union mount('{entry['name']}', style='collar') onto "
            "the inside face and pass depth = wall + collar grip, or plan on a drop "
            "of glue."
        )

    bore_d = float(entry["dome_diameter_mm"]) + 2.0 * press
    counter_d = float(entry["flange_diameter_mm"]) + 2.0 * slide
    lead_d = float(entry["leads"]["pitch_mm"]) + 2.0 * loose + 1.0

    stages = [
        _stage_row(entry["cutout"]["stages"][0], {"diameter_mm": bore_d}, prof,
                   -grip, 0.0),
        _stage_row(entry["cutout"]["stages"][1], {"diameter_mm": counter_d}, prof,
                   -(grip + flange_h), -grip),
    ]
    total = grip + flange_h
    if relief > 0.0:
        stages.append(
            _stage_row(entry["cutout"]["stages"][2], {"diameter_mm": lead_d}, prof,
                       -(total + relief), -total)
        )
        total += relief

    return {
        "style": "bore",
        "depth_mm": _round(grip),
        "total_depth_mm": _round(total),
        "bore_diameter_mm": _round(bore_d),
        "counterbore_diameter_mm": _round(counter_d),
        "counterbore_depth_mm": _round(flange_h),
        "lead_relief_mm": _round(relief),
        "grip_mm": _round(grip),
        "grip_target_mm": LED_GRIP_TARGET_MM,
        "press_fit_note": (
            f"press_fit in this profile is a {press:g} mm clearance PER SIDE, not an "
            "interference. That is deliberate: an FDM hole prints about that much "
            "undersize, so a bore modelled at "
            f"{bore_d:g} mm comes out near {entry['dome_diameter_mm']:g} mm and grips. "
            "If your printer holds holes true, the LED will be loose -- drop the "
            "tolerance or use a drop of glue, and either way test one before you "
            "print the finished body."
        ),
        "stages": stages,
        "notes": notes,
    }


def _panel_cutout_plan(entry: Mapping[str, Any], prof: Mapping[str, Any],
                       depth: Optional[float]) -> Dict[str, Any]:
    slide = _tol("slide_fit", prof)
    thread_d = float(entry["thread_diameter_mm"])
    spec_hole = float(entry["cutout"]["panel_hole_floor_mm"])
    fit_hole = thread_d + 2.0 * slide
    hole_d = max(spec_hole, fit_hole)

    thin = float(entry["panel_thickness_min_mm"])
    thick = float(entry["panel_thickness_max_mm"])
    wall = float(depth) if depth is not None else thick
    wall = _positive(wall, "depth")
    if wall > thick + 1e-9:
        raise PrintabilityError(
            f"{entry['name']} clamps through a panel {thin:g} to {thick:g} mm thick, "
            f"and this one is {wall:g} mm. Its {entry['thread_length_mm']:g} mm "
            "thread will not reach far enough for the nut to bite. Thin the wall "
            f"there to {thick:g} mm or less -- a local rebate around the hole is the "
            "usual fix -- or pick a switch with a longer neck."
        )
    notes: List[str] = []
    if wall < thin - 1e-9:
        notes.append(
            f"a {wall:g} mm panel is thinner than the {thin:g} mm this switch expects; "
            "it will clamp, but a 500 gf press on a thin printed wall flexes it. "
            "Add a boss or a rib behind the hole."
        )
    notes.append(
        f"the switch needs {entry['behind_panel_depth_mm']:g} mm of clear depth "
        f"behind the panel and a {entry['body_diameter_mm']:g} mm circle to sit in. "
        "Check that before you route wires past it."
    )
    return {
        "style": "panel_hole",
        "hole_diameter_mm": _round(hole_d),
        "hole_diameter_from_spec_mm": _round(spec_hole),
        "hole_diameter_from_fit_mm": _round(fit_hole),
        "hole_source": (
            "the larger of the switch's own panel-hole spec and (thread + 2 x "
            "printer.tolerances.slide_fit) -- a printed hole needs the printer's "
            "clearance whatever the datasheet says"
        ),
        "panel_thickness_mm": _round(wall),
        "panel_thickness_range_mm": [thin, thick],
        "behind_panel_depth_mm": float(entry["behind_panel_depth_mm"]),
        "behind_panel_diameter_mm": float(entry["body_diameter_mm"]),
        "depth_mm": _round(wall),
        "total_depth_mm": _round(wall),
        "stages": [
            _stage_row(entry["cutout"]["stages"][0], {"diameter_mm": hole_d}, prof,
                       -wall, 0.0)
        ],
        "notes": notes,
    }


def _pocket_cutout_plan(entry: Mapping[str, Any], prof: Mapping[str, Any],
                        depth: Optional[float]) -> Dict[str, Any]:
    stages: List[Dict[str, Any]] = []
    total = 0.0
    # ``depth`` sets the FIRST stage -- the seat the body drops into -- and the
    # relief stages behind it shift down with it.  Scaling every stage together
    # would make a deeper seat also mean a deeper lead relief, which is not what
    # anybody means by "make the pocket deeper".
    shift = 0.0
    if depth is not None:
        wanted = _positive(depth, "depth")
        native = abs(float(entry["cutout"]["stages"][0]["z0_mm"]))
        shift = wanted - native
    for index, stage in enumerate(entry["cutout"]["stages"]):
        fit = stage.get("fit")
        gap = _tol(fit, prof) if fit else 0.0
        z0 = float(stage["z0_mm"]) - shift
        z1 = float(stage["z1_mm"]) - (shift if index else 0.0)
        if stage["shape"] == "cylinder":
            grown = {"diameter_mm": float(stage["diameter_mm"]) + 2.0 * gap}
        else:
            grown = {
                "length_mm": float(stage["length_mm"]) + 2.0 * gap,
                "width_mm": float(stage["width_mm"]) + 2.0 * gap,
            }
        stages.append(_stage_row(stage, grown, prof, z0, z1))
        total = max(total, -z0)
    return {
        "style": "pocket",
        "depth_mm": _round(total),
        "total_depth_mm": _round(total),
        "stages": stages,
        "notes": [
            "a pocket is a seat, not a fixing: it stops the part sliding sideways "
            "and nothing else. Screw it, clip it or glue it as well.",
            "depth= sets the seat the body drops into; the relief stages behind it "
            "move with it.",
        ],
    }


def _fixed_cutout_plan(entry: Mapping[str, Any], prof: Mapping[str, Any],
                       depth: Optional[float]) -> Dict[str, Any]:
    """Cutouts whose diameter is somebody else's standard, not our tolerance."""
    stage = entry["cutout"]["stages"][0]
    diameter = float(stage["diameter_mm"])
    native = (
        abs(float(stage["z1_mm"]) - float(stage["z0_mm"]))
        if stage["z0_mm"] is not None and stage["z1_mm"] is not None
        else None
    )
    if depth is None and native is None:
        raise PrintabilityError(
            f"cutout('{entry['name']}') needs depth= -- how far the hole has to go. "
            "A clearance hole's depth is a property of the part it passes through, "
            "not of the fastener."
        )
    span = _positive(depth, "depth") if depth is not None else native
    return {
        "style": "bore",
        "hole_diameter_mm": _round(diameter),
        "hole_source": entry.get("hole_source") or (
            "a published standard, so no printer tolerance is added to it"
        ),
        "depth_mm": _round(span),
        "total_depth_mm": _round(span),
        "stages": [_stage_row(stage, {"diameter_mm": diameter}, prof, -span, 0.0)],
        "notes": [str(entry.get("hole_source") or "")] if entry.get("hole_source") else [],
    }


def cutout_plan(
    name: Any,
    printer: Optional[Mapping[str, Any]] = None,
    *,
    depth: Optional[float] = None,
    lead_relief: float = 3.0,
) -> Dict[str, Any]:
    """The negative :func:`cutout` will build, with every fit named and sourced."""
    entry = _components.component(name)
    prof = _prof(printer)
    style = entry["cutout"]["style"]

    if style == "magnet_pocket":
        inner = _fl.magnet_pocket_plan(
            entry["diameter_mm"], entry["thickness_mm"], prof, available_depth=depth
        )
        body = {
            "style": "magnet_pocket",
            "depth_mm": inner["pocket_depth_mm"],
            "total_depth_mm": inner["pocket_depth_mm"],
            "pocket_diameter_mm": inner["pocket_diameter_mm"],
            "forge_lib_plan": inner,
            "stages": [
                _stage_row(entry["cutout"]["stages"][0],
                           {"diameter_mm": inner["pocket_diameter_mm"]}, prof,
                           -inner["pocket_depth_mm"], 0.0)
            ],
            "notes": [
                "built by forge_lib.magnet_pocket, so it is sized exactly like "
                "/segment's magnet joints. Pass depth= the material under the mouth "
                "and it will refuse rather than punch through the back."
            ],
        }
    elif style == "bore" and entry["category"] == "light":
        body = _led_cutout_plan(entry, prof, depth, lead_relief)
    elif style == "panel_hole":
        body = _panel_cutout_plan(entry, prof, depth)
    elif style == "bore":
        body = _fixed_cutout_plan(entry, prof, depth)
    else:
        body = _pocket_cutout_plan(entry, prof, depth)

    body.update({
        "component": entry["name"],
        "category": entry["category"],
        "datum": entry["cutout"]["datum_note"],
        "mouth_overshoot_mm": MOUTH_OVERSHOOT_MM,
        "min_wall_mm": float(prof["min_wall_thickness"]),
        "honesty": _components.honesty(entry),
        "clamped": body.get("clamped") or [],
    })
    return body


def cutout(
    name: Any,
    printer: Optional[Mapping[str, Any]] = None,
    *,
    depth: Optional[float] = None,
    lead_relief: float = 3.0,
) -> Any:
    """The mounting negative for a real component, **as a solid to subtract**.

    Placed like ``forge_lib.magnet_pocket``: the cavity hangs below Z = 0 with
    its mouth ``MOUTH_OVERSHOOT_MM`` above it, so you put it at the face it is
    bored into and it bores straight down::

        body -= Pos(x, y, top_z) * maker_lib.cutout("led_5mm", depth=wall + 3)

    Every clearance comes from ``printer.tolerances``, chosen per face by what
    the joint has to do:

    * **LEDs** get a ``press_fit`` lens bore, a ``slide_fit`` counterbore for
      the flange to seat on, and a ``loose_fit`` relief so the legs are not
      crushed.  ``depth`` is how much material the lens passes through.
    * **Switch and holder bodies** get a ``slide_fit`` pocket -- they must drop
      in without force, because forcing a moulded body cracks it -- and a
      ``loose_fit`` relief for the legs.
    * **Panel switches** get the larger of the datasheet's panel hole and
      ``thread + 2 x slide_fit``, and the helper **raises** if the wall is
      thicker than the switch's thread can clamp.
    * **Magnets** go through ``forge_lib.magnet_pocket`` unchanged.
    * **Heat-set inserts and M3 clearance holes** get *no* printer tolerance:
      those diameters are the insert maker's and ISO's, and adding our clearance
      to them would be adding clearance twice.
    """
    plan = cutout_plan(name, printer, depth=depth, lead_relief=lead_relief)
    from build123d import Box, Cylinder, Pos  # noqa: PLC0415

    solids: List[Any] = []
    for index, stage in enumerate(plan["stages"]):
        z0 = float(stage["z0_mm"])
        z1 = float(stage["z1_mm"]) + (MOUTH_OVERSHOOT_MM if index == 0 else 0.0)
        height = z1 - z0
        if height <= 1e-9:
            continue
        if stage["shape"] == "cylinder":
            solids.append(
                Pos(0.0, 0.0, z0 + height / 2.0)
                * Cylinder(radius=float(stage["diameter_mm"]) / 2.0, height=height)
            )
        else:
            solids.append(
                Pos(0.0, 0.0, z0 + height / 2.0)
                * Box(float(stage["length_mm"]), float(stage["width_mm"]), height)
            )
    return _attach(_union(solids), "forge_cutout_plan", plan)


# ==========================================================================
# mount -- the positive
# ==========================================================================


def mount_plan(
    name: Any,
    printer: Optional[Mapping[str, Any]] = None,
    *,
    style: Optional[str] = None,
    height: Optional[float] = None,
    wall: Optional[float] = None,
    grip: float = LED_GRIP_TARGET_MM,
    standing: bool = False,
) -> Dict[str, Any]:
    """The positive :func:`mount` will build."""
    entry = _components.component(name)
    prof = _prof(printer)
    styles = tuple(entry.get("mount_styles") or ())
    if not styles:
        raise ComponentError(
            f"{entry['name']} has no mount() styles -- it is held by its cutout "
            f"alone. The components with mounts are: "
            + ", ".join(
                sorted(
                    other for other, record in _components.COMPONENTS.items()
                    if record.get("mount_styles")
                )
            )
        )
    chosen = str(style).strip().lower() if style is not None else styles[0]
    if chosen not in styles:
        raise ComponentError(
            f"{entry['name']} has no mount style {chosen!r}; its styles are "
            f"{', '.join(styles)}"
        )

    wall_floor = float(prof["min_wall_thickness"])
    feature = float(prof["min_feature_size"])
    slide = _tol("slide_fit", prof)
    clamped: List[str] = []
    thickness = float(wall) if wall is not None else max(2.0 * wall_floor, 1.6)
    thickness = _positive(thickness, "wall")
    if thickness < wall_floor - 1e-9:
        clamped.append(f"wall {thickness:g} -> {wall_floor:g} mm: the printer's minimum wall")
        thickness = wall_floor

    body: Dict[str, Any]
    if chosen == "collar":
        engagement = _positive(grip, "grip")
        flange_h = float(entry["flange_height_mm"])
        outer = float(entry["flange_diameter_mm"]) + 2.0 * slide + 2.0 * thickness
        body = {
            "style": "collar",
            "outer_diameter_mm": _round(outer),
            "height_mm": _round(engagement + flange_h),
            "grip_mm": _round(engagement),
            "wall_mm": _round(thickness),
            "usage": (
                f"Union it onto the INSIDE face of the wall, centred on the LED's "
                f"axis, then subtract cutout('{entry['name']}', depth=wall + "
                f"{engagement:g}) at the OUTSIDE face. The collar is solid -- the "
                "cutout does all the boring, so the two can never disagree."
            ),
            "support_free": True,
            "support_note": (
                "a plain cylinder standing off a flat face. If the wall it sits on "
                "is not flat, blend it in with a fillet or it prints as a stack of "
                "unsupported rings."
            ),
        }
    elif chosen == "shelf":
        length = float(entry.get("body_length_mm"))
        width = float(entry.get("body_width_mm"))
        seat_h = float(height) if height is not None else max(3.0, 2.0 * wall_floor)
        seat_h = _positive(seat_h, "height")
        rib = max(thickness, feature)
        body = {
            "style": "shelf",
            "pocket_length_mm": _round(length + 2.0 * slide),
            "pocket_width_mm": _round(width + 2.0 * slide),
            "rib_mm": _round(rib),
            "outer_length_mm": _round(length + 2.0 * slide + 2.0 * rib),
            "outer_width_mm": _round(width + 2.0 * slide + 2.0 * rib),
            "height_mm": _round(seat_h),
            "wall_mm": _round(rib),
            "usage": (
                f"A pad with a {rib:g} mm rib round it. Union it where the switch "
                "sits; the switch drops into the well on a slide fit and the ribs "
                "stop it walking sideways. It does not hold the switch DOWN -- a "
                "dab of hot glue, or the plunger's own guide sitting over it, does "
                "that."
            ),
            "support_free": True,
            "support_note": "an upward-facing well: every internal face is vertical or up.",
        }
    elif chosen == "pocket_boss":
        length = float(entry.get("body_length_mm"))
        width = float(entry.get("body_width_mm"))
        boss_h = float(height) if height is not None else float(entry["body_height_mm"]) + wall_floor
        boss_h = _positive(boss_h, "height")
        body = {
            "style": "pocket_boss",
            "outer_length_mm": _round(length + 2.0 * slide + 2.0 * thickness),
            "outer_width_mm": _round(width + 2.0 * slide + 2.0 * thickness),
            "height_mm": _round(boss_h),
            "wall_mm": _round(thickness),
            "usage": (
                f"A solid block to union on, then bore with "
                f"cutout('{entry['name']}'). Use it when the switch must be buried "
                "in material rather than sitting on a shelf."
            ),
            "support_free": True,
            "support_note": "solid until you bore it; the bore opens upward.",
        }
    elif chosen == "screw":
        pitch = float(entry.get("mount_hole_pitch_mm") or 0.0)
        hole_d = float(entry.get("mount_hole_diameter_mm") or 2.2)
        boss_h = float(height) if height is not None else 4.0
        screw_d = max(hole_d - 0.2, feature)
        inner = _fl.screw_boss_plan(screw_d, boss_h, printer=prof, style="thread-forming")
        body = {
            "style": "screw",
            "count": 2,
            "pitch_mm": _round(pitch),
            "screw_diameter_mm": _round(screw_d),
            "boss": inner,
            "height_mm": _round(boss_h),
            "wall_mm": _round(inner["wall_mm"]),
            "usage": (
                f"Two forge_lib.screw_boss bosses on the component's own "
                f"{pitch:g} mm mounting-hole pitch, centred on the part's centre. "
                "Union them where the component sits and drive an M2 self-tapping "
                "screw through each ear."
            ),
            "support_free": True,
            "support_note": inner.get("support_free"),
        }
    elif chosen == "rib":
        length = float(entry.get("body_length_mm"))
        width = float(entry.get("body_width_mm"))
        depth = float(entry["body_height_mm"])
        rib = max(thickness, feature)
        if standing:
            # The holder on edge: what it slides between is its THICKNESS, and
            # what stands up is its length.  This is how a 26 x 24 mm holder
            # fits inside a figure that is not 70 mm wide -- lying flat it needs
            # its whole diagonal of floor, on edge it needs 26 x 6.
            gap = depth + 2.0 * slide
            rib_len = length
            rib_h = float(height) if height is not None else max(0.5 * width, 8.0)
            stands = f"{width:g} mm of it stands above the floor"
        else:
            gap = length + 2.0 * slide
            rib_len = width
            rib_h = float(height) if height is not None else depth
            stands = "it lies flat"
        body = {
            "style": "rib",
            "standing": bool(standing),
            "count": 2,
            "rib_mm": _round(rib),
            "gap_mm": _round(gap),
            "length_mm": _round(rib_len),
            "height_mm": _round(rib_h),
            "wall_mm": _round(rib),
            "footprint_length_mm": _round(rib_len),
            "footprint_width_mm": _round(gap + 2.0 * rib),
            "usage": (
                f"Two ribs the component slides between ({stands}), open at both "
                "ends so the cell can still come out sideways. Union them on the "
                "floor. Ribs are a guide, not a grip -- the lid, a clip or a dab "
                "of glue has to stop the holder lifting out."
            ),
            "support_free": True,
            "support_note": "two vertical walls; nothing overhangs.",
        }
    elif chosen == "boss":
        if entry["kind"] == "heat_set_insert":
            hole_d = float(entry["hole_diameter_mm"])
            hole_depth = float(entry["hole_depth_mm"])
            boss_h = float(height) if height is not None else hole_depth + wall_floor
            boss_h = _positive(boss_h, "height")
            boss_wall = max(thickness, float(entry["min_boss_wall_mm"]))
            if boss_wall > thickness + 1e-9:
                clamped.append(
                    f"wall {thickness:g} -> {boss_wall:g} mm: a heat-set insert "
                    "melts the plastic around it, so it needs more wall than a "
                    "screw does"
                )
            outer = max(hole_d + 2.0 * boss_wall, float(entry["min_boss_diameter_mm"]))
            if boss_h < hole_depth + wall_floor - 1e-9:
                raise PrintabilityError(
                    f"a {boss_h:g} mm boss cannot take a {entry['name']}: the insert "
                    f"needs {hole_depth:g} mm of hole and the printer needs "
                    f"{wall_floor:g} mm of floor under it. Make the boss at least "
                    f"{hole_depth + wall_floor:.2f} mm tall."
                )
            body = {
                "style": "boss",
                "outer_diameter_mm": _round(outer),
                "height_mm": _round(boss_h),
                "hole_diameter_mm": _round(hole_d),
                "hole_depth_mm": _round(hole_depth),
                "hole_source": entry["hole_source"],
                "wall_mm": _round(boss_wall),
                "floor_mm": _round(boss_h - hole_depth),
                "usage": (
                    "Union it where the fastener goes, then press the insert in with "
                    "a soldering iron at about 220 C, straight down, and let it cool "
                    "before you screw anything into it. This boss does NOT go through "
                    "forge_lib.screw_boss: that helper derives its hole from the "
                    "screw and the printer, and a heat-set hole is neither -- it is "
                    "the insert maker's number and a deliberate interference."
                ),
                "support_free": True,
                "support_note": "a vertical cylinder with a vertical bore; nothing overhangs.",
            }
        else:
            screw_d = float(entry["thread_diameter_mm"])
            boss_h = float(height) if height is not None else 8.0
            inner = _fl.screw_boss_plan(screw_d, boss_h, printer=prof,
                                        wall=wall, style="thread-forming")
            body = {
                "style": "boss",
                "outer_diameter_mm": inner["boss_diameter_mm"],
                "height_mm": inner["height_mm"],
                "hole_diameter_mm": inner["hole_diameter_mm"],
                "hole_depth_mm": inner["hole_depth_mm"],
                "hole_source": (
                    "forge_lib.screw_boss, thread-forming: 0.80 x the screw diameter"
                ),
                "wall_mm": inner["wall_mm"],
                "floor_mm": inner["floor_mm"],
                "boss": inner,
                "usage": (
                    "forge_lib.screw_boss, unchanged. Union it where the screw goes "
                    "and drive an M3 self-tapper straight into the plastic. Good for "
                    "a handful of assemblies; if the joint will be opened often, use "
                    "heat_set_m3 instead."
                ),
                "support_free": True,
                "support_note": "support-free by construction (see screw_boss).",
            }
    else:  # pragma: no cover - guarded above
        raise ComponentError(f"mount style {chosen!r} is not implemented")

    body.update({
        "component": entry["name"],
        "category": entry["category"],
        "datum": "Z = 0 is the face the mount stands on; it is built upward from there.",
        "min_wall_mm": wall_floor,
        "honesty": _components.honesty(entry),
        "clamped": clamped,
    })
    return body


def mount(
    name: Any,
    printer: Optional[Mapping[str, Any]] = None,
    *,
    style: Optional[str] = None,
    height: Optional[float] = None,
    wall: Optional[float] = None,
    grip: float = LED_GRIP_TARGET_MM,
    standing: bool = False,
) -> Any:
    """The printed positive that holds a component -- boss, shelf, collar or clip.

    Built standing on Z = 0 and going up, so you union it onto the face it grows
    out of.  ``plan["usage"]`` is a sentence saying exactly how it composes with
    the matching :func:`cutout`; read it, because "union the collar inside, bore
    from outside" is the kind of thing that is obvious once and never again.

    Styles are per component (``component(name)["mount_styles"]``): ``collar``
    for LEDs, ``shelf`` / ``pocket_boss`` for switches, ``screw`` / ``rib`` for
    holders and boxes, ``boss`` for fasteners.

    ``standing=True`` applies to ``rib`` and turns the component on edge: the
    ribs then straddle its **thickness** rather than its length.  It is the
    difference between a 26 x 24 mm coin-cell holder needing its whole diagonal
    of floor and needing a 26 x 6 mm strip of it, which is the difference
    between a figure 70 mm wide and one 45 mm wide.
    """
    plan = mount_plan(name, printer, style=style, height=height, wall=wall,
                      grip=grip, standing=standing)
    from build123d import Box, Cylinder, Pos  # noqa: PLC0415

    chosen = plan["style"]
    if chosen == "collar":
        height_mm = plan["height_mm"]
        solid = Pos(0.0, 0.0, height_mm / 2.0) * Cylinder(
            radius=plan["outer_diameter_mm"] / 2.0, height=height_mm
        )
    elif chosen == "shelf":
        height_mm = plan["height_mm"]
        rib = plan["rib_mm"]
        outer = Pos(0.0, 0.0, height_mm / 2.0) * Box(
            plan["outer_length_mm"], plan["outer_width_mm"], height_mm
        )
        well_h = height_mm - max(rib, plan["min_wall_mm"])
        if well_h > 1e-6:
            outer -= Pos(0.0, 0.0, height_mm - well_h / 2.0 + MOUTH_OVERSHOOT_MM / 2.0) * Box(
                plan["pocket_length_mm"], plan["pocket_width_mm"],
                well_h + MOUTH_OVERSHOOT_MM,
            )
        solid = outer
    elif chosen == "pocket_boss":
        height_mm = plan["height_mm"]
        solid = Pos(0.0, 0.0, height_mm / 2.0) * Box(
            plan["outer_length_mm"], plan["outer_width_mm"], height_mm
        )
    elif chosen == "screw":
        half = plan["pitch_mm"] / 2.0
        boss = _fl.screw_boss(
            plan["screw_diameter_mm"], plan["height_mm"], printer=printer,
            style="thread-forming",
        )
        solid = (Pos(-half, 0.0, 0.0) * boss) + (Pos(half, 0.0, 0.0) * boss)
    elif chosen == "rib":
        rib = plan["rib_mm"]
        half = plan["gap_mm"] / 2.0 + rib / 2.0
        one = Pos(0.0, 0.0, plan["height_mm"] / 2.0) * Box(
            rib, plan["length_mm"], plan["height_mm"]
        )
        solid = (Pos(-half, 0.0, 0.0) * one) + (Pos(half, 0.0, 0.0) * one)
    else:  # boss
        if "boss" in plan:
            solid = _fl.screw_boss(
                plan["boss"]["screw_diameter_mm"], plan["height_mm"],
                printer=printer, style=plan["boss"]["style"],
            )
        else:
            height_mm = plan["height_mm"]
            hole_depth = plan["hole_depth_mm"]
            hole_r = plan["hole_diameter_mm"] / 2.0
            lead = min(0.5, 0.25 * plan["wall_mm"])
            solid = _fl._revolve_profile([
                (0.0, 0.0),
                (plan["outer_diameter_mm"] / 2.0, 0.0),
                (plan["outer_diameter_mm"] / 2.0, height_mm),
                (hole_r + lead, height_mm),
                (hole_r, height_mm - lead),
                (hole_r, height_mm - hole_depth),
                (0.0, height_mm - hole_depth),
            ])
    return _attach(solid, "forge_mount_plan", plan)


# ==========================================================================
# plunger -- the push mechanic
# ==========================================================================


def plunger_plan(
    stem_d: float,
    travel: Optional[float] = None,
    *,
    switch: Any = "tactile_6x6_latching",
    printer: Optional[Mapping[str, Any]] = None,
    guide_length: Optional[float] = None,
    overtravel: float = DEFAULT_OVERTRAVEL_MM,
    free_play: float = DEFAULT_FREE_PLAY_MM,
    keyed: bool = True,
    cap_engagement: Optional[float] = None,
    tip_length: Optional[float] = None,
    fit: str = "slide_fit",
) -> Dict[str, Any]:
    """The kinematics :func:`plunger` will build.  Pure arithmetic, no kernel.

    Every number below is derived from the switch's own datasheet dimensions and
    the printer profile.  Nothing is a guess, and every clamp is named.
    """
    entry = _components.require_category(switch, "switch")
    actuation = entry["actuation"]
    _plungeable = [
        other for other in _components.catalog("switch")
        if _components.COMPONENTS[other]["kind"] == "tactile_6x6"
    ]
    if entry["kind"] == "slide_spdt":
        raise PrintabilityError(
            f"{entry['name']} is a SLIDE switch: it moves sideways, not down, so a "
            "plunger cannot operate it. Give it a slot in the wall wide enough for "
            f"its knob to travel {actuation['stroke_mm']:g} mm, or pick a switch a "
            "plunger can push: " + ", ".join(_plungeable)
        )
    if entry["kind"] == "panel_push_latching":
        raise PrintabilityError(
            f"{entry['name']} already IS the plunger. It clamps through the wall "
            f"with its own nut and its own {entry['cap_diameter_mm']:g} mm cap is "
            "what a finger touches, so putting a maker_lib plunger in front of it "
            "would be two sliding mechanisms in series -- twice the friction and "
            "twice the free play, for nothing. Use cutout('"
            f"{entry['name']}', depth=wall) for the panel hole, and if you want a "
            "decorative cap, model it with a socket over that "
            f"{entry['cap_diameter_mm']:g} mm cap. maker_lib.plunger is for "
            "switches that live INSIDE the body: " + ", ".join(_plungeable)
        )

    prof = _prof(printer)
    wall_floor = float(prof["min_wall_thickness"])
    clearance = _tol(fit, prof)
    clamped: List[str] = []
    notes: List[str] = []

    diameter = _positive(stem_d, "stem_d")
    if diameter < float(prof["min_feature_size"]) - 1e-9:
        raise PrintabilityError(
            f"a {diameter:g} mm plunger stem is under the printer's "
            f"{prof['min_feature_size']:g} mm minimum feature; it would print as a "
            "string. Use at least that, and 4 mm or more if a finger is pushing it."
        )

    # -- the switch's own numbers -----------------------------------------
    stroke_switch = float(actuation["stroke_mm"])
    max_over = float(actuation["max_overtravel_mm"])
    latching = bool(actuation["latching"])
    force_gf = float(actuation["force_gf"])
    button_h = float(entry["button_protrusion_mm"])
    body_h = float(entry["body_height_mm"])
    overall_h = float(entry["overall_height_mm"])

    # -- overtravel: clamped to what the switch can physically take --------
    requested_over = _positive(overtravel, "overtravel", allow_zero=True)
    over = requested_over
    if over > max_over + 1e-9:
        clamped.append(
            f"overtravel {requested_over:g} -> {max_over:g} mm: past that the "
            f"{entry['name']}'s button is already bottomed on its own body, and the "
            "end stop would be crushing the switch instead of protecting it"
        )
        over = max_over

    play = _positive(free_play, "free_play", allow_zero=True)

    # -- travel: the finger's working stroke, and the whole design travel ---
    stroke = stroke_switch + over
    design_travel = play + stroke
    if travel is not None:
        wanted = _positive(travel, "travel")
        if wanted < design_travel - 1e-9:
            clamped.append(
                f"travel {wanted:g} -> {design_travel:g} mm: the "
                f"{entry['name']} needs {stroke_switch:g} mm to actuate, plus "
                f"{over:g} mm of overtravel so a tolerance stack cannot leave the "
                f"press just short of the click, plus the {play:g} mm of free play "
                "that keeps the plunger off the button at rest"
            )
        elif wanted > design_travel + 1e-9:
            # A longer travel is legal: the extra is free play the finger moves
            # through before anything happens.  Say so rather than silently
            # changing the feel.
            play = wanted - stroke
            notes.append(
                f"travel {wanted:g} mm is longer than this switch needs "
                f"({design_travel:g} mm). The extra {wanted - design_travel:g} mm "
                "becomes free play: the flame moves that far before the switch "
                "feels anything. That is a legitimate way to make a small switch "
                "feel like a big press, but the plunger will rattle by that much."
            )
        design_travel = max(wanted, design_travel)

    # -- the guide: long enough that the plunger cannot cock and jam --------
    minimum_guide = GUIDE_ENGAGEMENT_RATIO * diameter
    guide = float(guide_length) if guide_length is not None else minimum_guide
    guide = _positive(guide, "guide_length")
    if guide < minimum_guide - 1e-9:
        clamped.append(
            f"guide_length {guide:g} -> {minimum_guide:g} mm: a pin engaged less "
            f"than {GUIDE_ENGAGEMENT_RATIO:g} x its diameter cocks in its bore and "
            "jams the first time somebody presses it off-centre"
        )
        guide = minimum_guide

    bore_d = diameter + 2.0 * clearance

    # The bore is not a circle when the stem is keyed: forge_lib's anti-rotation
    # rib pushes the widest part of the cavity out to (stem radius + key height +
    # clearance).  Size the sleeve and the flange off THAT, not off the bore
    # diameter, or the keyway cuts a slot straight through the sleeve wall and
    # the flange stops overlapping the rim it is supposed to catch on.
    key_spec = _fl.peg_spec(d=diameter, l=max(diameter, 1.0), key=keyed)
    key_reach = (float(key_spec["key_height"]) + clearance) if keyed else clearance
    bore_outer_r = diameter / 2.0 + key_reach

    # The sleeve is walled to the minimum FEATURE plus a margin, not to the
    # minimum wall: the keyway takes a slot out of it, and a nominal 1.2 mm wall
    # with a slot through it measures 0.95 mm once it is tessellated -- under the
    # minimum feature, which is a warning on a part that is otherwise clean.
    sleeve_wall = max(wall_floor, float(prof["min_feature_size"]) + 0.5, 1.2)
    sleeve_od = 2.0 * (bore_outer_r + sleeve_wall)

    flange_lip = max(wall_floor, 0.8)
    if flange_lip > sleeve_wall + 1e-9:
        clamped.append(
            f"flange lip {flange_lip:g} -> {sleeve_wall:g} mm: the flange has to "
            "land on the sleeve's bottom rim, so it cannot be wider than the sleeve"
        )
        flange_lip = sleeve_wall
    flange_d = 2.0 * (bore_outer_r + flange_lip)
    flange_t = max(wall_floor, 1.2)

    engagement = (
        _positive(cap_engagement, "cap_engagement")
        if cap_engagement is not None
        else max(3.0, 0.5 * diameter)
    )

    # -- the flange's lead cone -------------------------------------------
    #    The plunger prints standing on its tip, which means the flange is a
    #    step out from a 6 mm cylinder to a 10 mm one -- a flat annular ceiling
    #    in mid-air.  Sloping its underside at the profile's own overhang limit
    #    makes the whole piece support-free, and costs nothing: that face never
    #    touches anything.
    flange_step = (flange_d - diameter) / 2.0
    lead = 0.0
    if flange_step > 1e-6:
        limit = max(float(prof["max_unsupported_overhang_deg"]) - _fl.OVERHANG_SAFETY_DEG, 1.0)
        lead = flange_step / math.tan(math.radians(min(limit, 89.0)))

    # -- the tip: long enough that the flange never lands on the switch -----
    #    At full press the flange's underside must still clear the switch body,
    #    and the lead cone lives inside the tip's length.
    flange_gap = 1.0
    tip_floor = max(
        stroke + play - button_h + flange_gap,
        lead + float(prof["min_feature_size"]),
    )
    tip = float(tip_length) if tip_length is not None else max(tip_floor, button_h + 1.0)
    tip = _positive(tip, "tip_length")
    if tip < tip_floor - 1e-9:
        clamped.append(
            f"tip_length {tip:g} -> {tip_floor:g} mm: any shorter and either the "
            f"plunger's retention flange lands on the {entry['name']}'s body before "
            "the button is pressed, or the flange's support-free lead cone runs "
            "past the tip's own end"
        )
        tip = tip_floor

    # -- positions, all in the guide's frame (Z = 0 is the sleeve's bottom) --
    flange_top_z = 0.0
    flange_bottom_z = -flange_t
    tip_z = flange_bottom_z - tip
    button_top_z = tip_z - play
    switch_seat_z = button_top_z - overall_h
    switch_body_top_z = switch_seat_z + body_h
    cap_underside_z = guide + design_travel
    stem_top_z = cap_underside_z + engagement
    stem_length = stem_top_z - flange_bottom_z

    flange_at_press_z = flange_bottom_z - design_travel
    flange_clearance = flange_at_press_z - switch_body_top_z
    if flange_clearance < 0.0:  # pragma: no cover - tip_floor prevents it
        raise PrintabilityError(
            f"the retention flange would hit the {entry['name']}'s body "
            f"{-flange_clearance:.2f} mm before the button bottoms out. Lengthen "
            "tip_length or use a switch with a taller button."
        )

    # -- feel and force ----------------------------------------------------
    newtons = force_gf * 0.00980665
    force_note = (
        f"about {force_gf:g} gf ({newtons:.2f} N) at the fingertip, plus whatever "
        f"the {clearance:g} mm guide clearance adds in friction -- which is small "
        "if the bore prints clean and large if it prints hairy, so deburr the bore "
        "with a twist of the same-diameter drill bit by hand before you assemble. "
        f"The datasheet band for this switch is "
        f"{actuation['force_band_gf'][0]:g} to {actuation['force_band_gf'][1]:g} gf."
    )
    if stroke < MIN_SATISFYING_TRAVEL_MM - 1e-9:
        notes.append(
            f"{stroke:g} mm of working travel is under the {MIN_SATISFYING_TRAVEL_MM:g} "
            "mm a fingertip reads as a press. It will work; it will feel like "
            "pressing a wall. tactile_6x6_latching gives 1.5 mm through this same "
            "plunger; if you want a big obvious press, push_latching_12mm has 3 mm "
            "and mounts through the wall by itself, no plunger needed."
        )

    if latching:
        return_note = (
            f"The {entry['name']} is a LATCHING switch, so the return is the "
            "switch's own spring and there is no separate spring in this design. "
            f"Press: the button latches {stroke_switch:g} mm down and stays there, "
            f"and the plunger stays down with it -- the cap visibly sits "
            f"{stroke_switch:g} mm lower while the light is on, which is a feature, "
            "not a fault. Press again: the latch releases and the spring pushes the "
            "plunger back up until the cap is clear of the guide again. Nothing "
            "else holds the plunger up, which is why the free play matters: it "
            "guarantees the plunger is never resting hard enough on the button to "
            "hold the switch in."
        )
        latched_drop = stroke_switch
        latched_gap = design_travel - stroke_switch
    else:
        return_note = (
            f"The {entry['name']} is MOMENTARY, so its spring holds the plunger up "
            "and returns it the instant the finger leaves. The light is on only "
            "while it is held -- if you want push-on / push-off you need a latching "
            "switch (tactile_6x6_latching, push_latching_12mm) or electronics."
        )
        latched_drop = None
        latched_gap = None

    assembly = [
        f"Print the guide as part of the body and the plunger as its own piece "
        f"({stem_length + lead:.1f} mm long, standing on its TIP -- the flange's "
        "underside is coned so it prints that way with no supports).",
        "Feed the plunger UP through the guide from the INSIDE of the body. It "
        "only goes in that way: the flange is bigger than the bore, deliberately, "
        "and that is what stops it ever falling out the front.",
        f"Fit the switch on its seat first if the body will be closed afterwards -- "
        f"its seating face sits {abs(switch_seat_z):.1f} mm below the guide's "
        "bottom rim.",
        f"Press the cap (the decorative piece -- the flame) onto the "
        f"{engagement:.1f} mm of stem sticking out of the top. Use "
        f"plunger_cap_socket() for its socket so the fit matches. Do this LAST: "
        "once the cap is on, the plunger is captive between it and the flange.",
        f"Check the feel before glue: the cap should move {design_travel:.1f} mm "
        f"and stop dead on the guide's rim, and the switch should click "
        f"{play:.1f} mm into that movement.",
    ]

    return {
        "mechanism": "plunger",
        "switch": entry["name"],
        "switch_latching": latching,
        "switch_stroke_mm": _round(stroke_switch),
        "switch_max_overtravel_mm": _round(max_over),
        "switch_button_protrusion_mm": _round(button_h),
        "switch_overall_height_mm": _round(overall_h),

        "stem_diameter_mm": _round(diameter),
        "bore_diameter_mm": _round(bore_d),
        "clearance_per_side_mm": _round(clearance),
        "fit": fit,
        "fit_source": f"printer.tolerances.{fit}",
        "keyed": bool(keyed),
        "keyed_note": (
            "the stem carries forge_lib's keyed-peg rib and the bore carries the "
            "matching slot, so the cap cannot rotate on its own axis -- which "
            "matters the moment the cap is a shape rather than a circle"
            if keyed
            else "round stem: the cap is free to spin. Fine for a knob, wrong for a flame."
        ),

        "guide_length_mm": _round(guide),
        "guide_engagement_mm": _round(guide),
        "guide_engagement_ratio": _round(guide / diameter),
        "guide_engagement_rule": (
            f"engagement >= {GUIDE_ENGAGEMENT_RATIO:g} x stem diameter, always -- "
            "the anti-cocking rule. The stem is longer than the sleeve over the "
            "whole travel, so the engagement is the sleeve's full length at every "
            "point of the press, not just at rest."
        ),
        "guide_outer_diameter_mm": _round(sleeve_od),
        "guide_wall_mm": _round(sleeve_wall),
        "bore_outer_radius_mm": _round(bore_outer_r),
        "key_reach_mm": _round(key_reach),

        "overtravel_mm": _round(over),
        "overtravel_requested_mm": _round(requested_over),
        "free_play_mm": _round(play),
        "free_play_note": (
            "the gap between the plunger's tip and the switch button when the "
            "plunger is lifted against its flange. It exists so no stack-up of "
            "printed tolerances can leave the plunger preloading the switch -- a "
            "model that is permanently on. Gravity closes it, so the plunger's "
            "resting place is on the button. It is also exactly how far the "
            "plunger can be pulled up before the flange catches."
        ),
        "stroke_mm": _round(stroke),
        "travel_mm": _round(design_travel),
        "end_stop_gap_mm": _round(design_travel),
        "end_stop_note": (
            f"the cap's underside sits {design_travel:g} mm above the guide's top "
            f"rim and lands on it. That rim is the hard stop, so the most the "
            f"switch can ever see is {stroke_switch + over:g} mm -- its "
            f"{stroke_switch:g} mm of stroke plus {over:g} mm of overtravel -- "
            "however hard the press. A finger can push 5 kg through a plunger; the "
            "end stop is the only reason that does not reach the switch."
        ),
        "switch_max_compression_mm": _round(stroke_switch + over),
        "switch_compression_headroom_mm": _round(max_over - over),
        "end_stop_protects_switch": True,

        "flange_diameter_mm": _round(flange_d),
        "flange_thickness_mm": _round(flange_t),
        "flange_lip_mm": _round(flange_lip),
        "flange_lead_mm": _round(lead),
        "flange_lead_note": (
            "the flange's underside is a cone, not a step, so the plunger prints "
            "standing on its tip with nothing unsupported. Print it that way: tip "
            "on the bed, stem in the air. Upside down the flange's flat TOP face "
            "becomes the ceiling instead and you are back where you started."
        ),
        "print_orientation": "tip on the bed, stem upward",
        "retention_note": (
            f"the flange is {flange_d:g} mm across and the bore is {bore_d:g} mm, "
            f"so the plunger cannot come out the front. It can rise {play:g} mm "
            "before the flange catches on the sleeve's bottom rim, and no further."
        ),

        "tip_length_mm": _round(tip),
        "stem_length_mm": _round(stem_length),
        "cap_engagement_mm": _round(engagement),

        "flange_top_z_mm": _round(flange_top_z),
        "flange_bottom_z_mm": _round(flange_bottom_z),
        "stem_tip_z_mm": _round(tip_z),
        "stem_top_z_mm": _round(stem_top_z),
        "cap_underside_z_mm": _round(cap_underside_z),
        "switch_button_top_z_mm": _round(button_top_z),
        "switch_seat_z_mm": _round(switch_seat_z),
        "switch_body_top_z_mm": _round(switch_body_top_z),
        "flange_clearance_at_full_press_mm": _round(flange_clearance),
        "frame_note": (
            "Z = 0 is the guide sleeve's BOTTOM rim -- the inside face of the wall "
            "the plunger passes through. The guide and the plunger come back in "
            "that one frame, at rest, so placing both with the same Pos() puts the "
            "assembly where you want it. Every z above is in that frame; the "
            "negative ones are inside the body."
        ),

        "latched_drop_mm": _round(latched_drop) if latched_drop is not None else None,
        "latched_cap_gap_mm": _round(latched_gap) if latched_gap is not None else None,
        "return": return_note,
        "force_note": force_note,
        "assembly": assembly,

        "min_wall_mm": wall_floor,
        "honesty": _components.honesty(entry),
        "notes": notes,
        "clamped": clamped,
    }


def plunger(
    stem_d: float,
    travel: Optional[float] = None,
    *,
    switch: Any = "tactile_6x6_latching",
    printer: Optional[Mapping[str, Any]] = None,
    guide_length: Optional[float] = None,
    overtravel: float = DEFAULT_OVERTRAVEL_MM,
    free_play: float = DEFAULT_FREE_PLAY_MM,
    keyed: bool = True,
    cap_engagement: Optional[float] = None,
    tip_length: Optional[float] = None,
    fit: str = "slide_fit",
) -> Dict[str, Any]:
    """**The push mechanic.**  A guided plunger that operates a real switch.

    Returns a dict of three things::

        rig = maker_lib.plunger(6.0, switch="tactile_6x6_latching")
        rig["guide"]     # positive: the sleeve, union it into the body's wall
        rig["plunger"]   # positive: the sliding piece, printed on its own
        rig["plan"]      # the kinematics, every number of it

    Both solids come back **in the same frame, in the at-rest position**, with
    ``Z = 0`` at the guide sleeve's bottom rim.  Place them with one ``Pos`` and
    the assembly lands together; ``plan["switch_seat_z_mm"]`` then says exactly
    where the switch's seat has to be in that same frame.

    What is engineered here, rather than left as a gap
    --------------------------------------------------
    * **Travel** is the switch's own actuation stroke plus overtravel, and the
      overtravel is clamped down to what the switch can physically take -- a 6x6
      tactile bottoms out 0.2 mm past actuation and driving it further crushes
      the dome.
    * **The end stop** is the cap's underside landing on the guide's top rim, so
      the switch can never see more than its stroke plus that overtravel however
      hard the press.  This is the number that decides whether the toy survives
      a child.
    * **Guide engagement** is never less than ``2 x`` the stem diameter, because
      a shorter pin cocks in its bore and jams on the first off-centre push.
    * **Retention** is a flange wider than the bore, so the plunger cannot leave
      through the front -- which also means it must be fitted from the inside
      *before* the body is closed, and the plan's ``assembly`` list says so.
    * **The return** is the switch's own spring.  There is no second spring in
      this design and there does not need to be; ``plan["return"]`` spells out
      what that means for a latching switch, which stays down while it is on.

    The decorative cap -- the flame -- is yours to model.  Take its socket from
    :func:`plunger_cap_socket` so the press fit matches, and put its underside
    at ``plan["cap_underside_z_mm"]``.
    """
    plan = plunger_plan(
        stem_d, travel, switch=switch, printer=printer, guide_length=guide_length,
        overtravel=overtravel, free_play=free_play, keyed=keyed,
        cap_engagement=cap_engagement, tip_length=tip_length, fit=fit,
    )

    from build123d import Cylinder, Pos  # noqa: PLC0415

    diameter = plan["stem_diameter_mm"]
    guide_len = plan["guide_length_mm"]
    clearance = plan["clearance_per_side_mm"]
    keyed_now = plan["keyed"]

    # The stem carries the lead-in chamfer (peg() puts it on the top edge), which
    # is what the cap's square-mouthed socket slides over.
    spec = _fl.peg_spec(d=diameter, l=plan["stem_length_mm"], key=keyed_now)

    # -- the guide sleeve: a tube with the keyed bore, bottom rim on Z = 0 --
    sleeve = Pos(0.0, 0.0, guide_len / 2.0) * Cylinder(
        radius=plan["guide_outer_diameter_mm"] / 2.0, height=guide_len
    )
    bore = _fl.socket_for(
        _fl.peg_spec(d=diameter, l=guide_len, key=keyed_now, chamfer=0.0),
        tolerance=clearance,
        depth_extra=2.0 * MOUTH_OVERSHOOT_MM,
        mouth_chamfer=clearance,
    )
    guide = sleeve - Pos(0.0, 0.0, -MOUTH_OVERSHOOT_MM) * bore

    # -- the plunger: coned flange + keyed stem + tip, at rest ------------
    stem = Pos(0.0, 0.0, plan["flange_bottom_z_mm"]) * _fl.peg(spec)
    flange_bottom = plan["flange_bottom_z_mm"]
    flange_r = plan["flange_diameter_mm"] / 2.0
    stem_r = diameter / 2.0
    lead = plan["flange_lead_mm"]
    # A revolve rather than a cylinder, so the flange's underside is a slope the
    # printer can carry rather than an annular ceiling in mid-air.
    flange = Pos(0.0, 0.0, 0.0) * _fl._revolve_profile([
        (0.0, flange_bottom - lead),
        (stem_r, flange_bottom - lead),
        (flange_r, flange_bottom),
        (flange_r, flange_bottom + plan["flange_thickness_mm"]),
        (0.0, flange_bottom + plan["flange_thickness_mm"]),
    ])
    tip = Pos(
        0.0, 0.0, plan["stem_tip_z_mm"] + plan["tip_length_mm"] / 2.0
    ) * Cylinder(radius=stem_r, height=plan["tip_length_mm"])
    piece = stem + flange + tip

    _attach(guide, "forge_plunger_plan", plan)
    _attach(piece, "forge_plunger_plan", plan)
    return {"guide": guide, "plunger": piece, "plan": plan}


def plunger_cap_socket(
    plan: Mapping[str, Any],
    printer: Optional[Mapping[str, Any]] = None,
    *,
    fit: str = "press_fit",
) -> Any:
    """The negative to subtract from the decorative cap so it presses onto the stem.

    Built like ``forge_lib.socket_for``: base on Z = 0, opening **downward**, so
    you subtract it at the cap's underside::

        flame -= Pos(0, 0, 0) * maker_lib.plunger_cap_socket(rig["plan"])

    with the flame modelled so its underside is that Z = 0 plane.  Placed in the
    plunger's own frame, that plane is ``plan["cap_underside_z_mm"]``.
    """
    prof = _prof(printer)
    clearance = _tol(fit, prof)
    spec = _fl.peg_spec(
        d=float(plan["stem_diameter_mm"]),
        l=float(plan["cap_engagement_mm"]),
        key=bool(plan["keyed"]),
        chamfer=0.0,
    )
    # No mouth chamfer. A cone widening into the shoulder face leaves exactly the
    # feather edge forge_lib.blunted_taper exists to prevent -- measured at 0.01 mm
    # on the first cut of this. The lead-in belongs on the STEM, where peg()
    # already puts it, not on the hole.
    cavity = _fl.socket_for(spec, tolerance=clearance, depth_extra=0.4,
                            mouth_chamfer=0.0)
    from build123d import Rot  # noqa: PLC0415

    socket = Rot(180.0, 0.0, 0.0) * cavity
    return _attach(socket, "forge_cap_socket_plan", {
        "stem_diameter_mm": plan["stem_diameter_mm"],
        "engagement_mm": plan["cap_engagement_mm"],
        "clearance_per_side_mm": _round(clearance),
        "fit": fit,
        "fit_source": f"printer.tolerances.{fit}",
        "keyed": bool(plan["keyed"]),
        "datum": (
            "Z = 0 is the cap's underside -- the face that lands on the guide's top "
            "rim and stops the press. The socket opens downward from it."
        ),
        "note": (
            "press fit plus a drop of glue is the right answer here. The cap is the "
            "part a finger pushes thousands of times, and a cap that comes off in "
            "your hand takes the plunger's retention with it."
        ),
    })


# ==========================================================================
# snap_clip -- a cantilever that bends and does not break
# ==========================================================================


def material(name: Any = DEFAULT_MATERIAL) -> Dict[str, Any]:
    """One row of :data:`MATERIALS`, by name (case-insensitive)."""
    key = str(name).strip().upper()
    if key not in MATERIALS:
        raise PrintabilityError(
            f"no snap-fit numbers on file for {name!r}; the materials are "
            f"{', '.join(sorted(MATERIALS))}"
        )
    record = dict(MATERIALS[key])
    record["material"] = key
    return record


def _taper_k(ratio: float) -> float:
    low, high = 0.5, 1.0
    span = high - low
    weight = (min(max(ratio, low), high) - low) / span
    return _TAPER_K[0.5] + weight * (_TAPER_K[1.0] - _TAPER_K[0.5])


def snap_clip_plan(
    length: float,
    thickness: float,
    width: float,
    deflection: float,
    *,
    printer: Optional[Mapping[str, Any]] = None,
    material_name: str = DEFAULT_MATERIAL,
    taper: float = 1.0,
    lead_angle_deg: float = 30.0,
    return_angle_deg: float = 45.0,
) -> Dict[str, Any]:
    """The cantilever :func:`snap_clip` will build, with the beam-bend maths.

    The rule that decides everything::

        y_max = K * strain_limit * L^2 / t

    -- the deflection at which the surface strain at the arm's root reaches the
    material's allowable.  ``K`` is 0.67 for a constant-section arm and 1.09 for
    one tapered to half thickness at the tip, because a constant-section beam
    carries its whole strain at the root and wastes the rest of its length.

    A requested deflection over ``y_max`` is **clamped**, and the clamp is
    reported: an over-flexed PLA arm does not bend less, it snaps at the root.
    """
    prof = _prof(printer)
    wall_floor = float(prof["min_wall_thickness"])
    feature = float(prof["min_feature_size"])
    slide = _tol("slide_fit", prof)
    mat = material(material_name)
    clamped: List[str] = []
    notes: List[str] = []

    arm_l = _positive(length, "length")
    arm_t = _positive(thickness, "thickness")
    arm_b = _positive(width, "width")
    nib = _positive(deflection, "deflection")

    if arm_t < wall_floor - 1e-9:
        clamped.append(f"thickness {arm_t:g} -> {wall_floor:g} mm: the printer's minimum wall")
        arm_t = wall_floor
    if arm_b < feature - 1e-9:
        clamped.append(
            f"width {arm_b:g} -> {feature:g} mm: an arm narrower than the minimum "
            "feature is a string, not a beam"
        )
        arm_b = feature

    ratio = float(taper)
    if not 0.5 <= ratio <= 1.0:
        clamped.append(
            f"taper {ratio:g} -> {min(max(ratio, 0.5), 1.0):g}: the design charts "
            "cover a tip between half the root thickness and the full thickness; "
            "thinner than half and the tip is too weak to carry the nib"
        )
        ratio = min(max(ratio, 0.5), 1.0)
    tip_t = arm_t * ratio
    if tip_t < wall_floor - 1e-9:
        new_ratio = wall_floor / arm_t
        clamped.append(
            f"taper {ratio:g} -> {new_ratio:.3f}: a {tip_t:.2f} mm tip is under the "
            f"printer's {wall_floor:g} mm minimum wall"
        )
        ratio = min(new_ratio, 1.0)
        tip_t = arm_t * ratio

    k = _taper_k(ratio)
    max_deflection = k * float(mat["strain"]) * arm_l * arm_l / arm_t

    if nib > max_deflection + 1e-9:
        clamped.append(
            f"deflection {nib:g} -> {max_deflection:.3f} mm: at {nib:g} mm this "
            f"{arm_l:g} x {arm_t:g} mm {mat['material']} arm reaches "
            f"{100.0 * nib * arm_t / (k * arm_l * arm_l):.2f}% surface strain at its "
            f"root, over the {100.0 * mat['strain']:.1f}% this material takes. It "
            "would not bend further -- it would break. Lengthen the arm (deflection "
            "goes with the SQUARE of length), thin it, or taper it"
        )
        nib = max_deflection
    if nib < MIN_NIB_MM - 1e-9:
        raise PrintabilityError(
            f"a {nib:.3f} mm catch is not a catch, it is a texture: nothing will "
            f"hold on it. This arm can only bend {max_deflection:.3f} mm before it "
            f"breaks, so make it longer -- {math.sqrt(MIN_NIB_MM * arm_t / (k * mat['strain'])):.1f} "
            f"mm would give the {MIN_NIB_MM:g} mm minimum -- or print it in PETG, "
            "which takes nearly twice the strain PLA does."
        )

    strain = nib * arm_t / (k * arm_l * arm_l)
    modulus = float(mat["modulus_mpa"])
    friction = float(mat["friction"])

    # Deflection force for a constant-section cantilever, N (mm, MPa, N).
    stiffness = arm_b * arm_t ** 3 * modulus / (4.0 * arm_l ** 3)
    deflect_n = stiffness * nib

    def _mate(angle_deg: float) -> Optional[float]:
        angle = math.radians(min(max(angle_deg, 0.0), 89.0))
        tangent = math.tan(angle)
        bottom = 1.0 - friction * tangent
        if bottom <= 1e-6:
            return None
        return deflect_n * (friction + tangent) / bottom

    insertion_n = _mate(lead_angle_deg)
    retention_n = _mate(return_angle_deg)
    if retention_n is None:
        notes.append(
            f"a {return_angle_deg:g} degree return face is a PERMANENT snap: the "
            "friction angle exceeds the ramp, so the arm cannot be pushed back out "
            "of the catch. Fit it once, or cut it out. Use 45 degrees or less if "
            "you want to open it again."
        )

    nib_height = max(2.0 * nib, feature)
    notes.append(
        "PRINT ORIENTATION IS THE WHOLE GAME. Print the arm so the layers run "
        "ALONG it, not across it: a layer boundary at the root is a crack that has "
        "already started, and a clip printed the wrong way round snaps on the first "
        "flex whatever the arithmetic says."
    )
    notes.append(
        f"These numbers are {mat['material']} design rules of thumb, not a "
        f"measurement of your spool ({mat['note']}). Print one clip and flex it "
        "before you print thirty."
    )

    return {
        "mechanism": "snap_clip",
        "material": mat["material"],
        "material_note": mat["note"],
        "strain_limit": mat["strain"],
        "strain_at_deflection": _round(strain),
        "strain_utilisation": _round(strain / float(mat["strain"])),
        "modulus_mpa": modulus,
        "friction_coefficient": friction,

        "length_mm": _round(arm_l),
        "root_thickness_mm": _round(arm_t),
        "tip_thickness_mm": _round(tip_t),
        "taper": _round(ratio),
        "taper_k": _round(k),
        "taper_k_source": (
            "standard snap-fit deflection charts: K = 0.67 for a constant section, "
            "1.09 for a tip half the root thickness; anything between is "
            "interpolated"
        ),
        "width_mm": _round(arm_b),

        "deflection_mm": _round(nib),
        "max_deflection_mm": _round(max_deflection),
        "deflection_rule": "y_max = K * strain_limit * L^2 / t",
        "nib_height_mm": _round(nib_height),

        "deflection_force_n": _round(deflect_n),
        "insertion_force_n": _round(insertion_n) if insertion_n else None,
        "retention_force_n": _round(retention_n) if retention_n else None,
        "lead_angle_deg": _round(lead_angle_deg),
        "return_angle_deg": _round(return_angle_deg),
        "permanent": retention_n is None,

        "catch_length_mm": _round(arm_b + 2.0 * slide),
        "catch_depth_mm": _round(nib + slide),
        "catch_height_mm": _round(nib_height + 2.0 * slide),
        "catch_fit": "slide_fit",
        "catch_fit_source": "printer.tolerances.slide_fit",

        "datum": (
            "Z = 0 is the arm's root. The arm runs up +Z and the nib faces +X, so "
            "the clip deflects in -X to go in."
        ),
        "min_wall_mm": wall_floor,
        "notes": notes,
        "clamped": clamped,
    }


def snap_clip(
    length: float,
    thickness: float,
    width: float,
    deflection: float,
    *,
    printer: Optional[Mapping[str, Any]] = None,
    material_name: str = DEFAULT_MATERIAL,
    taper: float = 1.0,
    lead_angle_deg: float = 30.0,
    return_angle_deg: float = 45.0,
) -> Dict[str, Any]:
    """A cantilever snap-fit arm that is checked against the material's strain limit.

    Returns ``{"clip": <positive>, "catch": <negative>, "plan": {...}}`` -- the
    arm to union onto one piece and the window to subtract from the other, both
    sized from the same numbers so they cannot disagree.

    The arm's root is on Z = 0 and it runs up ``+Z``, with the nib on ``+X``.
    """
    plan = snap_clip_plan(
        length, thickness, width, deflection, printer=printer,
        material_name=material_name, taper=taper,
        lead_angle_deg=lead_angle_deg, return_angle_deg=return_angle_deg,
    )

    from build123d import Box, Plane, Polygon, Pos, Rectangle, extrude, loft  # noqa: PLC0415

    arm_l = plan["length_mm"]
    root_t = plan["root_thickness_mm"]
    tip_t = plan["tip_thickness_mm"]
    arm_b = plan["width_mm"]
    nib = plan["deflection_mm"]
    nib_h = plan["nib_height_mm"]

    if abs(tip_t - root_t) > 1e-6:
        try:
            arm = loft([
                Plane.XY * Rectangle(root_t, arm_b),
                Plane.XY.offset(arm_l) * Rectangle(tip_t, arm_b),
            ])
        except Exception:  # noqa: BLE001 - a straight arm is still a clip
            plan.setdefault("clamped", []).append(
                f"taper {plan['taper']:g} -> 1: the kernel would not loft this arm, "
                "so it came back constant-section (and stiffer than planned)"
            )
            arm = Pos(0.0, 0.0, arm_l / 2.0) * Box(root_t, arm_b, arm_l)
    else:
        arm = Pos(0.0, 0.0, arm_l / 2.0) * Box(root_t, arm_b, arm_l)

    # The nib: a wedge on +X at the tip.  Lead face on top (the ramp the catch
    # pushes the arm back along), return face underneath.
    lead_rise = nib / max(math.tan(math.radians(plan["lead_angle_deg"])), 1e-3)
    return_drop = nib / max(math.tan(math.radians(plan["return_angle_deg"])), 1e-3)
    x0 = root_t / 2.0 - 0.01
    z_top = arm_l
    z_mid = max(z_top - lead_rise, z_top - nib_h + 0.01)
    z_low = max(z_mid - max(return_drop, 0.0), 0.0)
    try:
        face = Plane.XZ * Polygon(
            (x0, z_top), (x0 + nib, z_mid), (x0, z_low), align=None
        )
        wedge = extrude(face, amount=arm_b / 2.0, both=True)
        clip = arm + wedge
    except Exception:  # noqa: BLE001 - a square nib still catches
        plan.setdefault("clamped", []).append(
            "nib ramp -> square: the kernel would not build the wedge, so the nib "
            "came back as a plain block. It will still catch; it will be harder to "
            "push in"
        )
        clip = arm + (
            Pos(x0 + nib / 2.0, 0.0, z_top - nib_h / 2.0) * Box(nib, arm_b, nib_h)
        )

    catch = Pos(
        x0 + plan["catch_depth_mm"] / 2.0, 0.0, z_top - plan["catch_height_mm"] / 2.0
    ) * Box(plan["catch_depth_mm"], plan["catch_length_mm"], plan["catch_height_mm"])

    _attach(clip, "forge_snap_plan", plan)
    _attach(catch, "forge_snap_plan", plan)
    return {"clip": clip, "catch": catch, "plan": plan}


# ==========================================================================
# battery_door
# ==========================================================================

_DOOR_STYLES = ("magnet", "screw")


def battery_door_plan(
    opening_length: float,
    opening_width: float,
    *,
    printer: Optional[Mapping[str, Any]] = None,
    style: str = "magnet",
    thickness: Optional[float] = None,
    lip: Optional[float] = None,
    magnet: str = "magnet_6x3",
    ledge_thickness: Optional[float] = None,
    corner_r: float = 1.5,
    through_depth: float = 40.0,
) -> Dict[str, Any]:
    """The door and its opening, as :func:`battery_door` will build them."""
    chosen = str(style).strip().lower()
    if chosen not in _DOOR_STYLES:
        raise PrintabilityError(
            f"battery_door style must be one of {', '.join(_DOOR_STYLES)}, got {style!r}"
        )
    prof = _prof(printer)
    wall_floor = float(prof["min_wall_thickness"])
    feature = float(prof["min_feature_size"])
    slide = _tol("slide_fit", prof)
    clamped: List[str] = []
    notes: List[str] = []

    open_l = _positive(opening_length, "opening_length")
    open_w = _positive(opening_width, "opening_width")

    plate_t = float(thickness) if thickness is not None else max(2.0 * wall_floor, 1.6)
    plate_t = _positive(plate_t, "thickness")
    if plate_t < wall_floor - 1e-9:
        clamped.append(f"thickness {plate_t:g} -> {wall_floor:g} mm: the printer's minimum wall")
        plate_t = wall_floor

    band = float(lip) if lip is not None else max(3.0, 2.0 * wall_floor)
    band = _positive(band, "lip")

    fixings: Dict[str, Any] = {}
    if chosen == "magnet":
        magnet_entry = _components.require_category(magnet, "magnet")
        magnet_d = float(magnet_entry["diameter_mm"])
        magnet_t = float(magnet_entry["thickness_mm"])
        needed_band = magnet_d + 2.0 * wall_floor
        if band < needed_band - 1e-9:
            clamped.append(
                f"lip {band:g} -> {needed_band:g} mm: a {magnet_d:g} mm magnet needs "
                f"{wall_floor:g} mm of plastic each side of it, and the lip is the "
                "only place there is room"
            )
            band = needed_band
        # The pocket is deeper than the magnet -- forge_lib grows it by the
        # profile's magnet_pocket_extra -- so the plate has to clear the POCKET
        # plus a floor, not the magnet plus a floor.
        bare_pocket = _fl.magnet_pocket_plan(magnet_d, magnet_t, prof)
        needed_plate = float(bare_pocket["pocket_depth_mm"]) + wall_floor
        if plate_t < needed_plate - 1e-9:
            clamped.append(
                f"thickness {plate_t:g} -> {needed_plate:g} mm: a {magnet_t:g} mm "
                f"magnet needs a {bare_pocket['pocket_depth_mm']:g} mm pocket, and "
                f"the {wall_floor:g} mm of floor that hides it has to go somewhere"
            )
            plate_t = needed_plate
        ledge = (
            _positive(ledge_thickness, "ledge_thickness")
            if ledge_thickness is not None
            else needed_plate
        )
        pocket = _fl.magnet_pocket_plan(magnet_d, magnet_t, prof, available_depth=ledge)
        fixings = {
            "magnet": magnet_entry["name"],
            "count": 4,
            "pocket": pocket,
            "ledge_thickness_mm": _round(ledge),
            "pull_force_kg_each": magnet_entry["pull_force_kg"],
            "polarity_warning": magnet_entry["polarity"],
        }
        notes.append(
            "Glue the magnets in with CA, not hot glue, and check every pair snaps "
            "together BEFORE the glue goes on. A magnet glued in backwards is a "
            "door that pushes itself open and a part you cannot recover the magnet "
            "from."
        )
    else:
        screw_entry = _components.component("m3_screw")
        head_d = float(screw_entry["heads"]["pan"]["diameter_mm"])
        clearance_d = float(screw_entry["clearance_hole_normal_mm"])
        needed_band = head_d + 2.0 * wall_floor
        if band < needed_band - 1e-9:
            clamped.append(
                f"lip {band:g} -> {needed_band:g} mm: an M3 pan head is {head_d:g} mm "
                f"across and needs {wall_floor:g} mm of lip outside it"
            )
            band = needed_band
        boss_h = (
            _positive(ledge_thickness, "ledge_thickness")
            if ledge_thickness is not None
            else 8.0
        )
        boss = _fl.screw_boss_plan(3.0, boss_h, printer=prof, style="thread-forming")
        fixings = {
            "screw": "m3_screw",
            "count": 2,
            "clearance_hole_mm": _round(clearance_d),
            "clearance_hole_source": "ISO clearance hole -- no printer tolerance added",
            "boss": boss,
            "boss_height_mm": _round(boss_h),
        }
        notes.append(
            "Thread-forming into printed plastic is good for a handful of "
            "openings. If this door will come off every week, put heat_set_m3 "
            "inserts in the bosses instead -- mount('heat_set_m3', style='boss')."
        )

    door_l = open_l + 2.0 * band
    door_w = open_w + 2.0 * band
    radius = _positive(corner_r, "corner_r", allow_zero=True)
    max_r = min(door_l, door_w) / 2.0 - 1e-6
    if radius > max_r:
        clamped.append(f"corner_r {radius:g} -> {max_r:.3f} mm: half the shorter side")
        radius = max_r

    if min(open_l, open_w) < feature:
        raise PrintabilityError(
            f"a {open_l:g} x {open_w:g} mm opening is under the printer's "
            f"{feature:g} mm minimum feature -- there is nothing to reach through. "
            "Make it at least big enough for the cell you are changing: a CR2032 is "
            "20 mm across and fingers are wider than that."
        )

    rebate_l = door_l + 2.0 * slide
    rebate_w = door_w + 2.0 * slide

    notes.append(
        "Which way the ledge faces decides whether it prints. The rebate's floor is "
        "an upward face when the opening is in the TOP of the printed body and a "
        "90 degree ceiling when it is in the bottom. Print the body with this "
        "opening upward, or accept supports in it."
    )

    return {
        "mechanism": "battery_door",
        "style": chosen,
        "opening_length_mm": _round(open_l),
        "opening_width_mm": _round(open_w),
        "door_length_mm": _round(door_l),
        "door_width_mm": _round(door_w),
        "door_thickness_mm": _round(plate_t),
        "lip_mm": _round(band),
        "rebate_length_mm": _round(rebate_l),
        "rebate_width_mm": _round(rebate_w),
        "rebate_depth_mm": _round(plate_t),
        "through_depth_mm": _round(_positive(through_depth, "through_depth")),
        "through_note": (
            "how far the opening's through hole bores past the rebate. It is a "
            "negative, so over-long is safe and under-long is a door that opens "
            "onto solid plastic -- raise it if the wall there is thicker than "
            f"{through_depth:g} mm."
        ),
        "clearance_mm": _round(slide),
        "clearance_source": "printer.tolerances.slide_fit",
        "corner_r_mm": _round(radius),
        "fixings": fixings,
        "datum": (
            "Z = 0 is the body's OUTSIDE face at the opening. The opening negative "
            "bores down (-Z) through it; the door plate is built separately with "
            "its own base on Z = 0, flat, in its print orientation."
        ),
        "flush": True,
        "min_wall_mm": wall_floor,
        "notes": notes,
        "clamped": clamped,
    }


def battery_door(
    opening_length: float,
    opening_width: float,
    *,
    printer: Optional[Mapping[str, Any]] = None,
    style: str = "magnet",
    thickness: Optional[float] = None,
    lip: Optional[float] = None,
    magnet: str = "magnet_6x3",
    ledge_thickness: Optional[float] = None,
    corner_r: float = 1.5,
    through_depth: float = 40.0,
) -> Dict[str, Any]:
    """A flush battery door and the rebated opening it drops into.

    Returns::

        door = maker_lib.battery_door(30, 24, style="magnet")
        door["opening"]        # negative: subtract at the body's outside face
        door["door"]           # positive: the plate, printed flat, own piece
        door["frame_magnets"]  # negative: subtract at the rebate ledge (magnet style)
        door["bosses"]         # positive: union behind the ledge (screw style)
        door["plan"]

    The plate is ``lip`` wider than the opening all round and sits in a rebate
    the same depth as it is thick, so it finishes flush and cannot fall inward.
    The lip is where the fixings live, so it is **clamped up** to whatever the
    magnet or the screw head actually needs -- which is the number people get
    wrong, and the reason so many printed battery doors are held on with tape.

    Composed from ``forge_lib`` throughout: the magnet pockets are
    :func:`~service.forge_lib.magnet_pocket` (so ``available_depth`` still
    refuses to punch through the back) and the screw bosses are
    :func:`~service.forge_lib.screw_boss`.
    """
    plan = battery_door_plan(
        opening_length, opening_width, printer=printer, style=style,
        thickness=thickness, lip=lip, magnet=magnet,
        ledge_thickness=ledge_thickness, corner_r=corner_r,
        through_depth=through_depth,
    )

    from build123d import Axis, Box, Cylinder, GeomType, Pos, fillet  # noqa: PLC0415

    def _round_vertical(solid: Any, amount: float) -> Any:
        if amount <= 1e-6:
            return solid
        try:
            return fillet(solid.edges().filter_by(Axis.Z).filter_by(GeomType.LINE),
                          radius=amount)
        except Exception:  # noqa: BLE001 - a square door is still a door
            plan.setdefault("clamped", []).append(
                f"corner_r {amount:g} -> 0 mm: the kernel would not fillet this plate"
            )
            return solid

    plate_t = plan["door_thickness_mm"]
    door_solid = _round_vertical(
        Pos(0.0, 0.0, plate_t / 2.0)
        * Box(plan["door_length_mm"], plan["door_width_mm"], plate_t),
        plan["corner_r_mm"],
    )

    # The opening: a rebate down from the face, then the through hole.
    rebate = Pos(0.0, 0.0, MOUTH_OVERSHOOT_MM - (plate_t + MOUTH_OVERSHOOT_MM) / 2.0) * Box(
        plan["rebate_length_mm"], plan["rebate_width_mm"], plate_t + MOUTH_OVERSHOOT_MM
    )
    rebate = _round_vertical(rebate, plan["corner_r_mm"])
    through_h = plate_t + plan["through_depth_mm"]
    hole = Pos(0.0, 0.0, -plate_t - through_h / 2.0 + 0.01) * Box(
        plan["opening_length_mm"], plan["opening_width_mm"], through_h
    )
    opening = rebate + hole

    frame_magnets = None
    bosses = None
    fixings = plan["fixings"]
    if plan["style"] == "magnet":
        offset_x = (plan["door_length_mm"] - plan["lip_mm"]) / 2.0
        offset_y = (plan["door_width_mm"] - plan["lip_mm"]) / 2.0
        magnet_entry = _components.component(fixings["magnet"])
        door_pockets = []
        frame_pockets = []
        for sx in (-1.0, 1.0):
            for sy in (-1.0, 1.0):
                pocket = _fl.magnet_pocket(
                    magnet_entry["diameter_mm"], magnet_entry["thickness_mm"],
                    printer=printer,
                )
                door_pockets.append(Pos(sx * offset_x, sy * offset_y, plate_t) * pocket)
                frame_pockets.append(
                    Pos(sx * offset_x, sy * offset_y, -plate_t)
                    * _fl.magnet_pocket(
                        magnet_entry["diameter_mm"], magnet_entry["thickness_mm"],
                        printer=printer,
                        available_depth=fixings["ledge_thickness_mm"],
                    )
                )
        for pocket in door_pockets:
            door_solid -= pocket
        frame_magnets = _union(frame_pockets)
        plan["fixings"]["positions_mm"] = [
            [_round(sx * offset_x), _round(sy * offset_y)]
            for sx in (-1.0, 1.0) for sy in (-1.0, 1.0)
        ]
    else:
        offset_x = (plan["door_length_mm"] - plan["lip_mm"]) / 2.0
        boss = _fl.screw_boss(3.0, fixings["boss_height_mm"], printer=printer,
                              style="thread-forming")
        bosses = (Pos(-offset_x, 0.0, 0.0) * boss) + (Pos(offset_x, 0.0, 0.0) * boss)
        clearance_r = fixings["clearance_hole_mm"] / 2.0
        for sx in (-1.0, 1.0):
            door_solid -= Pos(sx * offset_x, 0.0, plate_t / 2.0) * Cylinder(
                radius=clearance_r, height=plate_t + 2.0
            )
        plan["fixings"]["positions_mm"] = [
            [_round(-offset_x), 0.0], [_round(offset_x), 0.0]
        ]

    _attach(door_solid, "forge_door_plan", plan)
    _attach(opening, "forge_door_plan", plan)
    return {
        "door": door_solid,
        "opening": opening,
        "frame_magnets": frame_magnets,
        "bosses": bosses,
        "plan": plan,
    }


# ==========================================================================
# Assembly, in words
# ==========================================================================


def assembly_steps(
    *,
    plunger_plan_: Optional[Mapping[str, Any]] = None,
    circuit: Optional[Mapping[str, Any]] = None,
    door_plan: Optional[Mapping[str, Any]] = None,
    pieces: Optional[Sequence[str]] = None,
) -> List[str]:
    """The whole build, mechanism and circuit together, as ordered sentences.

    The order matters more than any single step: the plunger goes in before the
    body closes, the circuit is tested before anything is glued, and the cap
    goes on last because it is what makes the plunger captive.
    """
    steps: List[str] = []
    if pieces:
        steps.append(
            "Print the pieces: " + ", ".join(str(piece) for piece in pieces) + ". "
            "Check each one against /check on its own -- they print in different "
            "orientations and an overhang answer for a compound of all of them "
            "means nothing."
        )
    if plunger_plan_:
        steps.append(
            "Clean the plunger bore before anything else: run a twist of paper or "
            "the shank of a drill the same size as the stem down it by hand until "
            "the stem slides freely under its own weight. The press should take "
            f"{plunger_plan_.get('force_note', 'a light touch').split(',')[0]}; a "
            "bore with one printing whisker left in it turns that into a press "
            "that sticks."
        )
    if circuit:
        steps.append(
            "Build and TEST the circuit on the bench, out of the model, following "
            "the wiring steps. Nothing goes inside a body until the LED lights."
        )
    if plunger_plan_:
        for step in plunger_plan_.get("assembly", ()):
            steps.append(str(step))
    if door_plan:
        if door_plan["style"] == "magnet":
            steps.append(
                "Fit the door magnets last and check every pair snaps rather than "
                "pushes before any glue touches them. "
                + str(door_plan["fixings"]["polarity_warning"])
            )
        else:
            steps.append(
                "Screw the door on with M3 self-tappers. Do them up until they "
                "stop, not until they feel tight -- a thread-formed hole in PLA "
                "strips at about a quarter turn past snug."
            )
    steps.append(
        "Close it up and press it. If the switch does not click, take the cap off "
        "and press the plunger with a fingernail: a plunger that works without the "
        "cap and not with it means the cap is bottoming on the guide too early, "
        "and the fix is to seat the cap a fraction further down the stem."
    )
    return steps


__all__ = [
    "CircuitError",
    "ComponentError",
    "DEFAULT_FREE_PLAY_MM",
    "DEFAULT_MATERIAL",
    "DEFAULT_OVERTRAVEL_MM",
    "GUIDE_ENGAGEMENT_RATIO",
    "LED_GRIP_TARGET_MM",
    "MATERIALS",
    "MIN_NIB_MM",
    "MIN_SATISFYING_TRAVEL_MM",
    "PrintabilityError",
    "assembly_steps",
    "battery_door",
    "battery_door_plan",
    "bill_of_materials",
    "catalog",
    "circuit_plan",
    "component",
    "cutout",
    "cutout_plan",
    "diagram_svg",
    "envelope",
    "envelope_plan",
    "led_forward_voltage",
    "material",
    "mount",
    "mount_plan",
    "plunger",
    "plunger_cap_socket",
    "plunger_plan",
    "preferred_resistor",
    "snap_clip",
    "snap_clip_plan",
    "wiring_steps",
]
