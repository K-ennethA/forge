"""Litwick lamp -- push the flame, the light comes on; press again, it goes off.

Four printed pieces, one script, one ``part`` selector -- the same convention
``push_lamp_core.py`` uses, adapted to this silhouette and these components:

    part = 0  ->  the base: tapered body, switch tower, AAA cradle, screw posts
    part = 1  ->  the lid: the plunger guide, the LED bore, the screw holes
    part = 2  ->  the plunger: stem, retention flange, tip
    part = 3  ->  the flame: the translucent cap, modelled TIP DOWN

Real parts, chosen first
-------------------------
* ``tactile_6x6_latching`` -- push-on/push-off, 1.5 mm of latch travel.
* ``aaa_pair_box`` -- 2xAAA, 3 V, 1000 mAh. Five times a coin cell's capacity,
  which is the whole reason it's in the drawer already. It has no internal
  resistance worth the name, which is why the circuit needs a resistor that a
  CR2032 design would not.
* ``led_5mm`` in warm white -- same 3.0 V forward drop as cool white.
* ``m3_screw`` -- two, holding the lid down into two printed bosses.

The AAA holder only has a ``"screw"`` mount style in the catalog (screwed flat
against a panel) -- there is no canned "stand it on edge" the way
``cr2032_holder``'s ``rib`` style gives for free. So the cradle below is hand
built from the holder's own real dimensions (``maker_lib.component``) and the
printer's own ``loose_fit`` clearance, in the same spirit as ``rib`` standing
mode: two thin rails the holder drops between, sized and spaced from its own
52 x 26 x 14 mm envelope, never from a typed number.

Base diameter is DERIVED, not chosen: the switch tower and the AAA cradle
have to stand side by side on the same floor, and ``_base`` raises with the
exact number if ``base_diameter`` is not wide enough for them. 60 mm is
this script's own answer for the parts in play here -- change the parts and
the number the raise gives you will change too.

Circuit
-------
``led_5mm`` warm white + ``aaa_pair_box`` + ``tactile_6x6_latching`` needs a
small resistor: the AAA pack has no meaningful internal resistance to protect
the LED the way a coin cell's ~30 ohm does, and a fresh pair can read enough
above nominal to push more current than the LED or the switch's 50 mA rating
can take. Call ``notes()`` for the exact value, the wiring steps and the bill
of materials.

Why the housing is two pieces
------------------------------
The switch has to face the plunger and the plunger has to come out of the
top; splitting at the mouth puts the switch's seat facing up out of the open
base and the guide standing up off a flat lid, and both halves print support
free. Battery access rides along for free: pop the same lid and the AAA
cradle is right there.
"""

import math

from build123d import *  # noqa: F403 - the build123d house style

import forge_lib  # noqa: F401 - provided by the geometry service
import maker_lib  # noqa: F401 - provided by the geometry service

PARAMS = {
    "part": {
        "value": 0,
        "unit": "count",
        "min": 0,
        "max": 3,
        "step": 1,
        "description": "Which piece: 0 base, 1 lid, 2 plunger, 3 flame cap",
    },
    "total_height": {
        "value": 95.0,
        "unit": "mm",
        "min": 70.0,
        "max": 160.0,
        "step": 1.0,
        "description": "Height of the base, floor to the rim the lid sits on",
    },
    "base_diameter": {
        "value": 60.0,
        "unit": "mm",
        "min": 46.0,
        "max": 100.0,
        "step": 1.0,
        "description": (
            "Diameter at the floor. Set by the AAA cradle standing beside the "
            "switch tower -- _base raises with the number if this is too small"
        ),
    },
    "wall": {
        "value": 3.0,
        "unit": "mm",
        "min": 2.0,
        "max": 6.0,
        "step": 0.2,
        "description": "Shell wall thickness",
    },
    "foot_count": {
        "value": 4,
        "unit": "count",
        "min": 0,
        "max": 6,
        "step": 1,
        "description": "Little foot nubs around the base (0 removes them)",
    },
    "lid_thickness": {
        "value": 3.0,
        "unit": "mm",
        "min": 2.0,
        "max": 6.0,
        "step": 0.2,
        "description": "Lid plate thickness -- also the LED's grip",
    },
    "stem_diameter": {
        "value": 6.0,
        "unit": "mm",
        "min": 4.0,
        "max": 10.0,
        "step": 0.5,
        "description": "Plunger stem; the guide is always at least twice this long",
    },
    "guide_length": {
        "value": 12.0,
        "unit": "mm",
        "min": 6.0,
        "max": 24.0,
        "step": 1.0,
        "description": "Guide sleeve length; raised to 2 x the stem if you ask for less",
    },
    "flame_height": {
        "value": 55.0,
        "unit": "mm",
        "min": 20.0,
        "max": 90.0,
        "step": 1.0,
        "description": "Height of the flame cap above its own base",
    },
    "flame_radius": {
        "value": 16.0,
        "unit": "mm",
        "min": 8.0,
        "max": 25.0,
        "step": 0.5,
        "description": "Radius of the flame at its widest",
    },
    "led_offset": {
        "value": 12.0,
        "unit": "mm",
        "min": 6.0,
        "max": 20.0,
        "step": 0.5,
        "description": "How far off the plunger's axis the LED sits under the lid",
    },
}

#: The parts this design is built around. Change these and every derived
#: number in the script -- travel, resistor, cradle size -- changes with them.
_SWITCH = "tactile_6x6_latching"
_LED = "led_5mm"
_LED_COLOR = "warm white"
_CELL = "aaa_pair_box"
_POST_SCREW = "m3_screw"

#: How far every cutter here pokes past the face it enters, so no boolean is
#: ever a coplanar-face boolean. Same idea as forge_lib.MOUTH_OVERSHOOT_MM.
_OVERSHOOT_MM = 0.2


def _rig(p):
    """The plunger mechanism. One call; everything else is placed off its plan."""
    return maker_lib.plunger(
        p["stem_diameter"], switch=_SWITCH, guide_length=p["guide_length"]
    )


def _body_profile(p):
    """Six (radius, z) control points: the waist at the floor, flaring to the rim."""
    base_r = p["base_diameter"] / 2.0
    rim_r = 1.5 * base_r
    H = p["total_height"]
    d = rim_r - base_r
    return [
        (base_r, 0.0),
        (base_r + 0.30 * d, 0.20 * H),
        (base_r + 0.55 * d, 0.42 * H),
        (base_r + 0.80 * d, 0.65 * H),
        (rim_r, 0.85 * H),
        (rim_r, H),
    ]


def _rim_radius(p):
    return 1.5 * (p["base_diameter"] / 2.0)


def _post_radius(p):
    """Where the two lid screws sit.

    The posts run the base's full height, floor to lid, and the body is
    narrowest at the floor (it flares outward above that) -- so the posts
    have to clear the FLOOR's radius, not the wider rim, or their base pokes
    through the thin wall down there.
    """
    wall = max(p["wall"], forge_lib.min_wall())
    base_r = p["base_diameter"] / 2.0
    return base_r - wall - 5.0


def _flame_geometry(p, plan):
    """Every number the flame and the lid have to agree on, in the lid's frame.

    Z = 0 is the lid's underside, so the plate is ``0 .. lid_thickness`` and
    the guide sleeve is ``0 .. guide_length``.
    """
    lid_t = max(p["lid_thickness"], forge_lib.min_wall())
    loose = forge_lib.fit_tolerance("loose_fit")
    wall_floor = forge_lib.min_wall()
    led = maker_lib.component(_LED)

    travel = plan["travel_mm"]
    gap = travel + forge_lib.fit_tolerance("slide_fit")

    shoulder_z = plan["guide_length_mm"] + travel
    rim_z = lid_t + gap
    skirt = shoulder_z - rim_z
    if skirt < forge_lib.min_feature():
        raise forge_lib.PrintabilityError(
            f"the guide stands {plan['guide_length_mm']:g} mm off the lid and the "
            f"flame's skirt only reaches {skirt:.2f} mm down over it, which is "
            "nothing. Lengthen guide_length -- the flame has to cover the sleeve "
            "and the LED, or both are on show."
        )

    cup_d = plan["guide_outer_diameter_mm"] + 2.0 * loose
    recess_d = led["dome_diameter_mm"] + 2.0 * loose
    offset = p["led_offset"]

    inner_limit = cup_d / 2.0 + recess_d / 2.0 + wall_floor
    if offset < inner_limit - 1e-9:
        raise forge_lib.PrintabilityError(
            f"led_offset {offset:g} mm puts the LED's recess into the pocket the "
            f"flame's skirt needs for the {plan['guide_outer_diameter_mm']:g} mm "
            f"guide sleeve. Move it out to at least {inner_limit:.1f} mm."
        )
    outer_limit = p["flame_radius"] - recess_d / 2.0 - wall_floor
    if offset > outer_limit + 1e-9:
        raise forge_lib.PrintabilityError(
            f"led_offset {offset:g} mm puts the LED's recess through the side of a "
            f"{p['flame_radius']:g} mm flame. Move it in to "
            f"{outer_limit:.1f} mm or widen flame_radius to "
            f"{offset + recess_d / 2.0 + wall_floor:.1f} mm."
        )

    return {
        "lid_thickness": lid_t,
        "skirt": skirt,
        "cup_diameter": cup_d,
        "recess_diameter": recess_d,
        "recess_depth": (lid_t + led["above_flange_mm"] + travel + 1.0 - rim_z),
        "led_offset": offset,
    }


def notes(p=None):
    """Circuit, wiring and assembly for this design.

    Not part of the PARAMS contract -- call it with the resolved params, or
    with nothing for the defaults, to get what to solder and in what order.
    """
    values = {name: spec["value"] for name, spec in PARAMS.items()} if p is None else p
    circuit = maker_lib.circuit_plan(
        led=_LED, color=_LED_COLOR, cell=_CELL, switch=_SWITCH
    )
    rig_plan = maker_lib.plunger_plan(
        values["stem_diameter"], switch=_SWITCH, guide_length=values["guide_length"]
    )
    return {
        "circuit": circuit,
        "wiring_steps": maker_lib.wiring_steps(circuit),
        "bill_of_materials": maker_lib.bill_of_materials(circuit),
        "kinematics": rig_plan,
        "assembly_steps": maker_lib.assembly_steps(
            plunger_plan_=rig_plan,
            circuit=circuit,
            pieces=("base (part 0)", "lid (part 1)", "plunger (part 2)",
                    "flame (part 3)"),
        ),
        "diagram_svg": maker_lib.diagram_svg(circuit),
    }


# --------------------------------------------------------------------------
# The four pieces
# --------------------------------------------------------------------------


def _base(p):
    """Part 0 -- the tapered body: switch tower, AAA cradle, screw posts."""
    wall = max(p["wall"], forge_lib.min_wall())
    profile = _body_profile(p)
    base_r = p["base_diameter"] / 2.0

    body = forge_lib.soft_body(profile, wall)
    inner_floor = forge_lib.soft_body_plan(profile, wall)["floor_mm"]

    if int(p["foot_count"]) > 0:
        body += forge_lib.feet_ring(base_r, 6.0, int(p["foot_count"]), style="pad")

    rig_plan = maker_lib.plunger_plan(
        p["stem_diameter"], switch=_SWITCH, guide_length=p["guide_length"]
    )
    rim_z = p["total_height"]
    seat_z = rim_z + rig_plan["switch_seat_z_mm"]  # switch_seat_z is negative

    # ---- the switch tower, on axis -----------------------------------
    shelf_plan = maker_lib.mount_plan(_SWITCH, style="shelf")
    shelf_base_z = seat_z - shelf_plan["rib_mm"]
    tower_h = shelf_base_z - inner_floor
    if tower_h < forge_lib.min_feature():
        raise forge_lib.PrintabilityError(
            f"the switch's seat lands {seat_z:.1f} mm up and the floor is at "
            f"{inner_floor:.1f} mm, which leaves no tower between them. Raise "
            "total_height, or shorten guide_length."
        )

    # ---- the AAA cradle, beside the tower ------------------------------
    # aaa_pair_box has no "rib" mount style, so this is hand built from its
    # own real dimensions: two thin rails spaced by its 14 mm thickness
    # (plus loose_fit), long enough to catch its 26 mm width, standing the
    # full 52 mm the box needs to be held upright.
    cell = maker_lib.component(_CELL)
    loose = forge_lib.fit_tolerance("loose_fit")
    rail_t = max(wall * 0.6, forge_lib.min_feature())
    rail_gap = cell["body_height_mm"] + 2.0 * loose
    rail_span = cell["body_width_mm"] + 2.0 * loose
    cradle_h = cell["body_length_mm"] + loose

    tower_half_y = shelf_plan["outer_width_mm"] / 2.0
    near_y = tower_half_y + forge_lib.min_feature()
    far_y = near_y + rail_gap

    inner_radius = base_r - wall
    reach = math.hypot(rail_span / 2.0, far_y + rail_t)
    if reach + forge_lib.min_wall() > inner_radius + 1e-9:
        needed = 2.0 * (reach + forge_lib.min_wall() + wall)
        raise forge_lib.PrintabilityError(
            f"the AAA cradle's far rail reaches {reach:.1f} mm from the axis and "
            f"the cavity is only {inner_radius:.1f} mm, so a rail would print "
            f"through the wall. Raise base_diameter to at least {needed:.1f} mm "
            "-- the holder is a bought part and its 52 x 26 x 14 mm is not "
            "negotiable."
        )

    for y in (near_y, far_y):
        body += Pos(0, y + rail_t / 2.0, inner_floor + cradle_h / 2.0) * Box(  # noqa: F405
            rail_span, rail_t, cradle_h
        )

    # ---- the tower itself, standing on the true floor -------------------
    body += Pos(0, 0, inner_floor + tower_h / 2.0) * Box(  # noqa: F405
        shelf_plan["outer_length_mm"], shelf_plan["outer_width_mm"], tower_h
    )
    body += Pos(0, 0, shelf_base_z) * maker_lib.mount(_SWITCH, style="shelf")  # noqa: F405

    # ---- two screw posts for the lid -------------------------------------
    post_h = rim_z - p["lid_thickness"] - inner_floor
    if post_h >= forge_lib.min_feature():
        post = maker_lib.mount(_POST_SCREW, style="boss", height=post_h)
        post_r = _post_radius(p)
        for side in (-1.0, 1.0):
            body += Pos(side * post_r, 0, inner_floor) * post  # noqa: F405

    return body


def _lid(p):
    """Part 1 -- the flat plate that carries the guide, the LED and the screws."""
    rig = _rig(p)
    geometry = _flame_geometry(p, rig["plan"])
    lid_t = geometry["lid_thickness"]
    radius = _rim_radius(p)

    plate = Pos(0, 0, lid_t / 2.0) * Cylinder(radius=radius, height=lid_t)  # noqa: F405
    plate += rig["guide"]

    plate -= Pos(0, geometry["led_offset"], lid_t) * maker_lib.cutout(  # noqa: F405
        _LED, depth=lid_t
    )

    post_r = _post_radius(p)
    for side in (-1.0, 1.0):
        plate -= Pos(side * post_r, 0, lid_t) * maker_lib.cutout(  # noqa: F405
            _POST_SCREW, depth=lid_t
        )

    # One hole for the LED's return lead to drop back into the base.
    plate -= Pos(0, -geometry["led_offset"], lid_t / 2.0) * Cylinder(  # noqa: F405
        radius=1.75, height=lid_t + 2.0
    )
    return plate


def _plunger(p):
    """Part 2 -- the sliding piece, moved to stand on its tip for printing."""
    rig = _rig(p)
    plan = rig["plan"]
    return Pos(0, 0, -plan["stem_tip_z_mm"]) * rig["plunger"]  # noqa: F405


def _flame(p):
    """Part 3 -- the translucent cap, modelled TIP DOWN, as it prints."""
    plan = maker_lib.plunger_plan(
        p["stem_diameter"], switch=_SWITCH, guide_length=p["guide_length"]
    )
    geometry = _flame_geometry(p, plan)
    height = p["flame_height"]
    radius = p["flame_radius"]
    tip_r = max(forge_lib.min_land() / 2.0, 1.5)

    flame = forge_lib.soft_body([
        (tip_r, 0.0),
        (radius * 0.30, height * 0.20),
        (radius * 0.62, height * 0.42),
        (radius * 0.90, height * 0.62),
        (radius, height * 0.78),
        (radius, height),
    ])

    skirt = geometry["skirt"]
    flame -= Pos(0, 0, height - skirt / 2.0) * Cylinder(  # noqa: F405
        radius=geometry["cup_diameter"] / 2.0, height=skirt + 2.0 * _OVERSHOOT_MM
    )
    flame -= Pos(0, 0, height - skirt) * maker_lib.plunger_cap_socket(plan)  # noqa: F405

    depth = geometry["recess_depth"] + _OVERSHOOT_MM
    flame -= Pos(  # noqa: F405
        0, geometry["led_offset"], height + _OVERSHOOT_MM - depth / 2.0
    ) * Cylinder(radius=geometry["recess_diameter"] / 2.0, height=depth)  # noqa: F405
    return flame


def build(p):
    which = int(round(p["part"]))
    if which == 0:
        return _base(p)
    if which == 1:
        return _lid(p)
    if which == 2:
        return _plunger(p)
    return _flame(p)
