"""Push lamp core -- the reference for a script written from ``maker_lib``.

Press the flame, the light comes on.  Press it again, it goes off.  That is the
whole product, and this script is its engine room: a hollow body with a real
CR2032 holder, a real latching switch, a real 5 mm LED, a guided plunger that
operates the switch, and the translucent cap the finger actually touches.

It is the generalised form of a Litwick lamp.  Nothing in it is decoration --
change the silhouette and the mechanism still works, because the mechanism is
computed from the *components'* dimensions rather than from the shape.

This script exists to be **imitated**, the way ``magnet_holder.py`` does for
``forge_lib``.  Every clearance in it comes from ``maker_lib`` or ``forge_lib``,
which get it from ``printer.json``.  There is not one hand-typed tolerance
anywhere below.

The design-around-components law
--------------------------------
The parts were chosen FIRST and the model was built to their dimensions:

* ``tactile_6x6_latching`` -- 6 x 6 mm self-locking tactile switch.  It has
  **1.5 mm** of latch travel, which is what makes the press feel like a press;
  an ordinary momentary 6x6 has 0.25 mm and feels like pushing a wall.
* ``cr2032_cell`` + ``cr2032_holder`` -- 3 V, 20 mm across, changeable.
* ``led_5mm`` in **white** -- and white is the reason there is no resistor in
  this circuit; see the wiring notes below.
* ``m3_screw`` -- two of them hold the lid down, into two printed bosses.

Every one of those has a ``verify_against_your_part`` sentence in the component
table, and the coin-cell holder's is not boilerplate: holders vary from 20 to 28
mm long. **Measure yours before you print the base.**

Four printed pieces, four print orientations
--------------------------------------------
``build()`` returns one thing, so the piece is a parameter -- the same
convention ``eevee_style_bowl_base.py`` uses:

    part = 0  ->  the base: body, switch tower, holder ribs, wire channel, posts
    part = 1  ->  the lid: the plunger guide, the LED bore, the screw holes
    part = 2  ->  the plunger: stem, retention flange, tip
    part = 3  ->  the flame: the translucent cap blank, modelled TIP DOWN

Check each one separately.  Each is modelled in the orientation it prints in,
which for the flame means upside down -- a shade prints tip-first, and modelling
it any other way would make ``/check``'s overhang answer a fiction.

Why the housing is two pieces
-----------------------------
Because it has to be, and the reason is worth understanding before you design
your own.  The switch has to face the plunger.  The plunger has to come out of
the top.  Put both in one closed body and one of the two -- the guide's mouth or
the switch's seat -- always ends up facing away from the bed, which is an
unsupported flat face in the middle of a print.  Splitting the housing at the
mouth puts the switch's seat facing **up** out of the open base and the guide
standing **up** off a flat lid, and both halves come out support-free.  Real
enclosures are two pieces for exactly this reason.

The circuit, for the defaults
-----------------------------
``maker_lib.circuit_plan()`` at the defaults (CR2032 + white 5 mm LED +
latching 6x6) answers **no resistor needed**: a white LED drops about 3.0 V and
a CR2032 gives 3.0 V, so there is nothing left for a resistor to drop.  The
cell's own 30 ohm internal resistance is the current limit -- a real limit, not
a hope, and the reason an LED taped to a coin cell glows for a day instead of
exploding.  Run ``circuit_plan()`` and ``wiring_steps()`` from the script's own
namespace to get the full text; ``notes()`` below returns both plus the
assembly order.

Swap the LED to red and the same call answers **56 ohm, resistor optional**.
Swap the cell to ``aaa_pair_box`` and it answers **56 ohm, required** -- two
AAAs will happily deliver the 20 mA that kills the LED, where a coin cell
cannot.  That is the whole lesson of ``circuit_plan``: the answer is a property
of the parts, not of the tutorial you last read.

What the checks say, at the defaults
------------------------------------
* **part 0, the base** -- ``bed_fit``, ``min_wall`` and ``watertight`` pass;
  ``overhangs`` **warns at 30.8 mm2**, and the warning is honest and expected.
  It is the wire channel: the groove runs under the switch tower and through
  one holder rib, and the material bridging over it is a 3.5 mm wide flat
  ceiling.  A 3.5 mm bridge is not a support case -- FDM crosses that without
  noticing -- and the alternative is wires draped across the switch, which is
  the one thing that reliably stops a plunger working.  Everything else on the
  part is vertical or upward: the body is a ``soft_body`` hollow shell, open by
  construction, and the tower, the ribs and the two screw posts all stand up off
  the floor.
* **part 1, the lid** -- all four pass.  A flat plate with a vertical cylinder
  on it and three holes through it.  The LED's flange counterbore and lead
  relief cut into air below the plate rather than into material, which is
  correct: the flange seats on the plate's underside and the plate's full
  thickness is the LED's grip.
* **part 2, the plunger** -- all four pass, and only because the retention
  flange's underside is a **cone** rather than a step.  A flange that steps
  straight out from the stem is a flat annular ceiling in mid-air;
  ``maker_lib.plunger`` slopes it at the profile's own overhang limit, which
  costs nothing because that face never touches anything.
* **part 3, the flame** -- all four pass.  Modelled tip-down, so the cup, the
  cap socket and the LED recess all bore downward from the open top and none of
  them leaves a ceiling.  Its contact patch on the bed is about 3 mm across:
  **print it with a brim.**

The same four results hold at both ends of every declared range -- a 34 mm body
with a 4 mm stem and a 3 mm LED, and a 90 mm body with a 12 mm stem and a 10 mm
LED -- and with the momentary switch instead of the latching one.

Three failures this script was rewritten to fix, all worth stealing
-------------------------------------------------------------------
1. **Every cutter overshoots the face it enters.** The wire groove's top face
   was originally exactly the floor plane. That is a coplanar-face boolean, and
   OCC left a 0.04 mm sliver there that read as both a thin wall and a 54 mm2
   flat ceiling.
2. **A ``soft_body``'s cavity floor is not flat.** The inward offset turns the
   corner near the wall, so the floor rises as it goes out. A groove cut to the
   *nominal* floor depth leaves a skin over its far end -- measured at 0.037 mm
   on a 40 mm body. Cut the slot proud of the floor by more than the dish.
3. **Cut into the shell, then stand things on it.** Subtracting the groove after
   unioning the tower and the ribs leaves slivers along every seam. Groove
   first, union second, and the tunnel comes out as one clean bridge.
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
        "description": "Which piece: 0 base, 1 lid, 2 plunger, 3 flame cap blank",
    },
    "body_height": {
        "value": 44.0,
        "unit": "mm",
        "min": 30.0,
        "max": 90.0,
        "step": 1.0,
        "description": "Height of the hollow body, base to rim",
    },
    "body_radius": {
        "value": 24.0,
        "unit": "mm",
        "min": 18.0,
        "max": 40.0,
        "step": 0.5,
        "description": (
            "Radius at the base and the rim; the waist swells past it. It is set "
            "by the coin-cell holder standing on edge beside the switch tower"
        ),
    },
    "body_swell": {
        "value": 5.0,
        "unit": "mm",
        "min": 0.0,
        "max": 12.0,
        "step": 0.5,
        "description": "How far the waist bulges out past the base radius",
    },
    "wall": {
        "value": 2.4,
        "unit": "mm",
        "min": 1.2,
        "max": 5.0,
        "step": 0.2,
        "description": "Wall of the hollow body; clamped up to the printer minimum",
    },
    "lid_thickness": {
        "value": 3.0,
        "unit": "mm",
        "min": 2.0,
        "max": 6.0,
        "step": 0.2,
        "description": (
            "Lid plate thickness -- also the LED's grip, so 3 mm is the floor "
            "worth having"
        ),
    },
    "stem_diameter": {
        "value": 6.0,
        "unit": "mm",
        "min": 4.0,
        "max": 12.0,
        "step": 0.5,
        "description": "Plunger stem; the guide is always at least twice this long",
    },
    "guide_length": {
        "value": 12.0,
        "unit": "mm",
        "min": 6.0,
        "max": 30.0,
        "step": 1.0,
        "description": "Guide sleeve length; raised to 2 x the stem if you ask for less",
    },
    "switch_variant": {
        "value": 0,
        "unit": "count",
        "min": 0,
        "max": 1,
        "step": 1,
        "description": "0 the latching 6x6 (push on / push off), 1 the momentary 9.5 mm",
    },
    "led_variant": {
        "value": 1,
        "unit": "count",
        "min": 0,
        "max": 2,
        "step": 1,
        "description": "0 a 3 mm LED, 1 a 5 mm LED, 2 a 10 mm LED",
    },
    "led_offset": {
        "value": 11.0,
        "unit": "mm",
        "min": 6.0,
        "max": 20.0,
        "step": 0.5,
        "description": (
            "How far off the plunger's axis the LED sits. It has to clear the "
            "guide sleeve inside the flame's skirt and still leave a wall at the "
            "flame's edge; the script raises with both numbers if it does not"
        ),
    },
    "flame_height": {
        "value": 38.0,
        "unit": "mm",
        "min": 20.0,
        "max": 70.0,
        "step": 1.0,
        "description": "Height of the flame cap blank",
    },
    "flame_radius": {
        "value": 16.0,
        "unit": "mm",
        "min": 8.0,
        "max": 25.0,
        "step": 0.5,
        "description": "Radius of the flame at its widest, which is its base",
    },
    "flame_gap": {
        "value": 3.0,
        "unit": "mm",
        "min": 1.0,
        "max": 8.0,
        "step": 0.2,
        "description": (
            "Gap under the flame's skirt when it is up. It is the visible seam, "
            "and it must exceed the plunger's travel or the flame bottoms on the "
            "lid instead of on the guide"
        ),
    },
    "wire_channel": {
        "value": 3.5,
        "unit": "mm",
        "min": 2.0,
        "max": 6.0,
        "step": 0.5,
        "description": "Width of the wire groove in the floor and the hole in the lid",
    },
}

#: The switches this design will drive, in ``switch_variant`` order.  Both are
#: 6 x 6 mm, so the tower and its shelf are the same either way -- only the feel
#: changes, and ``plunger_plan`` reports the difference.
_SWITCHES = ("tactile_6x6_latching", "tactile_6x6_h95")

#: The LEDs, in ``led_variant`` order.
_LEDS = ("led_3mm", "led_5mm", "led_10mm")

#: The colour the wiring notes assume.  White, because a white LED's 3.0 V
#: forward voltage against a 3 V cell is what makes the resistor unnecessary.
_LED_COLOR = "white"

#: The screws that hold the lid down, and their posts.
_POST_SCREW = "m3_screw"

#: How far every cutter in this script pokes past the face it enters, so no
#: boolean in it is ever a coplanar-face boolean.  Same value and same reason as
#: ``forge_lib.MOUTH_OVERSHOOT_MM``.
_OVERSHOOT_MM = 0.2


def _switch_name(p):
    return _SWITCHES[int(round(p["switch_variant"]))]


def _led_name(p):
    return _LEDS[int(round(p["led_variant"]))]


def _rig(p):
    """The plunger mechanism.  One call; everything else is placed off its plan."""
    return maker_lib.plunger(
        p["stem_diameter"],
        switch=_switch_name(p),
        guide_length=p["guide_length"],
    )


def _body_profile(p):
    """Five (radius, z) control points for the hollow body, listed base upward."""
    radius = p["body_radius"]
    swell = p["body_swell"]
    height = p["body_height"]
    return [
        (radius, 0.0),
        (radius + swell * 0.8, height * 0.18),
        (radius + swell, height * 0.40),
        (radius + swell * 0.35, height * 0.72),
        (radius, height),
    ]


def _post_radius(p):
    """Where the two lid screws sit: inboard of the wall, clear of the holder."""
    return p["body_radius"] - p["wall"] - 5.0


def _flame_geometry(p, plan):
    """Every number the flame and the lid have to agree on, in the lid's frame.

    The lid's frame: Z = 0 is the lid's underside, so the plate is
    ``0 .. lid_thickness`` and the guide sleeve is ``0 .. guide_length``.
    """
    lid_t = max(p["lid_thickness"], forge_lib.min_wall())
    loose = forge_lib.fit_tolerance("loose_fit")
    wall_floor = forge_lib.min_wall()
    led = maker_lib.component(_led_name(p))

    # The gap under the flame's skirt has to outlast the press, or the flame
    # lands on the lid instead of on the guide's rim and the end stop -- the one
    # thing protecting the switch -- never engages.
    travel = plan["travel_mm"]
    gap = max(p["flame_gap"], travel + forge_lib.fit_tolerance("slide_fit"))

    shoulder_z = plan["guide_length_mm"] + travel   # where the cap's shoulder sits
    rim_z = lid_t + gap                             # where the skirt's rim sits
    skirt = shoulder_z - rim_z
    if skirt < forge_lib.min_feature():
        raise forge_lib.PrintabilityError(
            f"the guide stands {plan['guide_length_mm']:g} mm off the lid and the "
            f"flame's skirt only reaches {skirt:.2f} mm down over it, which is "
            "nothing. Lengthen guide_length or close flame_gap -- the flame has to "
            "cover the sleeve and the LED, or both are on show."
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
        "gap": gap,
        "skirt": skirt,
        "cup_diameter": cup_d,
        "recess_diameter": recess_d,
        "recess_depth": (lid_t + led["above_flange_mm"] + travel + 1.0) - rim_z,
        "led_offset": offset,
    }


def notes(p=None):
    """Circuit, wiring and assembly for this design, ready to print or return.

    Not part of the PARAMS contract -- a helper the script carries so the same
    file that builds the geometry also answers "what do I solder, and in what
    order?".  Call it with the resolved params, or with nothing for the
    defaults.
    """
    values = {name: spec["value"] for name, spec in PARAMS.items()} if p is None else p
    circuit = maker_lib.circuit_plan(
        led=_led_name(values),
        color=_LED_COLOR,
        cell="cr2032_cell",
        switch=_switch_name(values),
    )
    rig_plan = maker_lib.plunger_plan(
        values["stem_diameter"],
        switch=_switch_name(values),
        guide_length=values["guide_length"],
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
    """Part 0 -- the hollow body with everything that stands on its floor."""
    wall = max(p["wall"], forge_lib.min_wall())
    channel_r = p["wire_channel"] / 2.0
    # The floor carries a groove, so it is thicker than the wall by enough that
    # the groove cannot take it under the minimum.  Ask forge_lib for the floor
    # rather than adding a number here.
    floor = wall + channel_r + forge_lib.min_wall()

    body = forge_lib.soft_body(_body_profile(p), wall, floor=floor)
    inner_floor = forge_lib.soft_body_plan(
        _body_profile(p), wall, floor=floor
    )["floor_mm"]

    rig_plan = maker_lib.plunger_plan(
        p["stem_diameter"], switch=_switch_name(p), guide_length=p["guide_length"]
    )

    # ---- where things sit, in the body's own frame -----------------------
    # The lid's underside lands on the rim, so the plunger's frame origin (the
    # guide sleeve's bottom rim) is the rim itself.
    rim_z = p["body_height"]
    seat_z = rim_z + rig_plan["switch_seat_z_mm"]   # switch_seat_z is negative

    # ---- the switch tower ------------------------------------------------
    shelf_plan = maker_lib.mount_plan(_switch_name(p), style="shelf")
    # mount("shelf") puts the switch's seat one rib above its own base.
    shelf_base_z = seat_z - shelf_plan["rib_mm"]
    tower_h = shelf_base_z - inner_floor
    if tower_h < forge_lib.min_feature():
        raise forge_lib.PrintabilityError(
            f"the switch's seat lands {seat_z:.1f} mm up and the floor is at "
            f"{inner_floor:.1f} mm, which leaves no tower between them. Raise "
            "body_height, or shorten guide_length -- the plunger has to reach "
            "down past its own guide before it touches the switch."
        )
    # ---- the coin-cell holder, ON EDGE between two ribs ------------------
    # Lying flat, a 26 x 24 mm holder needs its whole diagonal of floor and the
    # body would have to be 70 mm across to take it beside the tower. On edge it
    # needs a 26 x 10 mm strip, and the body stays a figure rather than a tin.
    rib_plan = maker_lib.mount_plan("cr2032_holder", style="rib", standing=True)
    holder_y = -(
        shelf_plan["outer_width_mm"] / 2.0
        + rib_plan["footprint_width_mm"] / 2.0
        + forge_lib.min_feature()
    )
    # Derive the body from the parts, never the other way round: the holder's
    # far corner is what decides how wide the body has to be, so check it and
    # say the number rather than quietly printing a rib through the wall.
    corner = math.hypot(
        rib_plan["footprint_length_mm"] / 2.0,
        abs(holder_y) + rib_plan["footprint_width_mm"] / 2.0,
    )
    inner_radius = p["body_radius"] - wall
    if corner + forge_lib.min_wall() > inner_radius + 1e-9:
        raise forge_lib.PrintabilityError(
            f"the coin-cell holder's ribs reach {corner:.1f} mm from the axis and "
            f"the cavity is only {inner_radius:.1f} mm, so a rib would print "
            "through the wall. Raise body_radius to at least "
            f"{corner + forge_lib.min_wall() + wall:.1f} mm, or use a smaller cell "
            "-- the holder is a bought part and its 26 x 24 mm is not negotiable."
        )
    # ---- the wire channel: a groove in the floor, open upward ------------
    # Cut FIRST, into the bare shell, and only then union the tower and the ribs
    # over the top of it. Cutting it last means subtracting a box out of a solid
    # whose floor already carries three unions, and OCC leaves 0.04 mm slivers
    # along the seams where they meet. Groove the floor, then stand things on it:
    # the tunnel under the tower comes out as one clean bridge.
    #
    # Square, not half-round, for the same family of reasons: a half-round groove
    # is tangent to the walls it passes and feathers out to nothing where it
    # meets them.
    #
    # And it is cut PROUD of the floor, not level with it. A soft_body's cavity
    # floor is slightly dished -- the inward offset turns the corner near the
    # wall, so the floor rises as it goes out -- and a groove whose top is the
    # nominal floor plane leaves a skin over its far end where the real floor sat
    # higher. Measured at 0.037 mm on a 40 mm body, which is a min_wall failure
    # for a face nobody drew.
    channel_len = abs(holder_y) + rib_plan["footprint_width_mm"] / 2.0
    channel_bottom = inner_floor - channel_r
    channel_top = inner_floor + channel_r + forge_lib.min_wall()
    channel_h = channel_top - channel_bottom
    body -= Pos(0, holder_y / 2.0, channel_bottom + channel_h / 2.0) * Box(  # noqa: F405
        p["wire_channel"], channel_len, channel_h
    )

    # ---- everything that stands on the floor -----------------------------
    body += Pos(0, 0, inner_floor + tower_h / 2.0) * Box(  # noqa: F405
        shelf_plan["outer_length_mm"], shelf_plan["outer_width_mm"], tower_h
    )
    body += Pos(0, 0, shelf_base_z) * maker_lib.mount(  # noqa: F405
        _switch_name(p), style="shelf"
    )
    body += Pos(0, holder_y, inner_floor) * Rot(0, 0, 90) * maker_lib.mount(  # noqa: F405
        "cr2032_holder", style="rib", standing=True
    )

    post_h = rim_z - p["lid_thickness"] - inner_floor
    if post_h >= forge_lib.min_feature():
        post = maker_lib.mount(_POST_SCREW, style="boss", height=post_h)
        for side in (-1.0, 1.0):
            body += Pos(side * _post_radius(p), 0, inner_floor) * post  # noqa: F405

    return body


def _lid(p):
    """Part 1 -- the flat plate that carries the guide, the LED and the screws."""
    rig = _rig(p)
    geometry = _flame_geometry(p, rig["plan"])
    lid_t = geometry["lid_thickness"]
    radius = p["body_radius"]

    plate = Pos(0, 0, lid_t / 2.0) * Cylinder(radius=radius, height=lid_t)  # noqa: F405

    # The guide's bottom rim sits on the plate's underside, so the sleeve and
    # the plate merge into one solid and the bore runs through both.
    plate += rig["guide"]

    # The LED: bored from the TOP face down. Its flange counterbore and lead
    # relief fall away below the plate into open air, which is exactly right --
    # the flange seats against the plate's underside, and the plate's full
    # thickness is the LED's grip.
    plate -= Pos(0, geometry["led_offset"], lid_t) * maker_lib.cutout(  # noqa: F405
        _led_name(p), depth=lid_t
    )

    # Two clearance holes for the screws into the base's posts.
    for side in (-1.0, 1.0):
        plate -= Pos(side * _post_radius(p), 0, lid_t) * maker_lib.cutout(  # noqa: F405
            _POST_SCREW, depth=lid_t
        )

    # One hole for the LED's return lead to drop back into the base.
    plate -= Pos(0, -geometry["led_offset"], lid_t / 2.0) * Cylinder(  # noqa: F405
        radius=p["wire_channel"] / 2.0, height=lid_t + 2.0
    )
    return plate


def _plunger(p):
    """Part 2 -- the sliding piece, moved to stand on its tip for printing."""
    rig = _rig(p)
    plan = rig["plan"]
    # plan["stem_tip_z_mm"] is the lowest point of the piece in the assembly
    # frame; dropping it onto Z = 0 is the orientation plan["print_orientation"]
    # asks for, and the only one where the coned flange does its job.
    return Pos(0, 0, -plan["stem_tip_z_mm"]) * rig["plunger"]  # noqa: F405


def _flame(p):
    """Part 3 -- the translucent cap blank, modelled TIP DOWN, as it prints.

    The flame is a **cup**, not a plug: its skirt comes down over the guide
    sleeve and over the LED, so neither is on show and the light is inside the
    translucent part where it belongs. What lands on the guide's top rim -- the
    end stop -- is the shoulder at the bottom of the stem socket, and that is
    the surface ``plunger_plan()["cap_underside_z_mm"]`` names.
    """
    plan = maker_lib.plunger_plan(
        p["stem_diameter"], switch=_switch_name(p), guide_length=p["guide_length"]
    )
    geometry = _flame_geometry(p, plan)
    height = p["flame_height"]
    radius = p["flame_radius"]
    tip_r = max(forge_lib.min_land() / 2.0, 1.5)

    # Listed from the TIP upward, because that is the way up it prints. Every
    # step out is gentle enough to carry itself, and the last two points hold
    # the full radius so the skirt is a straight wall rather than a taper.
    flame = forge_lib.soft_body([
        (tip_r, 0.0),
        (radius * 0.30, height * 0.20),
        (radius * 0.62, height * 0.42),
        (radius * 0.90, height * 0.62),
        (radius, height * 0.78),
        (radius, height),
    ])

    # The cup that swallows the guide sleeve, bored down from the open end. It
    # runs a hair PAST the socket's mouth at the bottom and past the rim at the
    # top: leave either flush and the boolean is coplanar with the socket's own
    # face, which comes back as a 0.01 mm disc of material nobody asked for.
    skirt = geometry["skirt"]
    flame -= Pos(0, 0, height - skirt / 2.0) * Cylinder(  # noqa: F405
        radius=geometry["cup_diameter"] / 2.0, height=skirt + 2.0 * _OVERSHOOT_MM
    )

    # The socket that presses onto the plunger's stem, at the top of that cup.
    flame -= Pos(0, 0, height - skirt) * maker_lib.plunger_cap_socket(plan)  # noqa: F405

    # A recess so the LED's dome has somewhere to go, deep enough that the
    # flame's full travel never lands on it.
    depth = geometry["recess_depth"] + _OVERSHOOT_MM
    flame -= Pos(  # noqa: F405
        0, geometry["led_offset"], height + _OVERSHOOT_MM - depth / 2.0
    ) * Cylinder(radius=geometry["recess_diameter"] / 2.0, height=depth)  # noqa: F405
    return flame


def build(p):
    """Return whichever piece ``part`` selects, in its own print orientation."""
    which = int(round(p["part"]))
    if which == 0:
        return _base(p)
    if which == 1:
        return _lid(p)
    if which == 2:
        return _plunger(p)
    return _flame(p)
