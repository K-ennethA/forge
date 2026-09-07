"""``components`` -- the real parts a maker design is built *around*.

This is the data half of maker mode.  ``maker_lib`` turns these records into
geometry; nothing in here imports build123d, so the table is cheap to read, easy
to test and safe to serve over HTTP.

The law this table exists to enforce
------------------------------------
**Pick the real part first, then model to its dimensions.**  A cavity invented
from nothing is a cavity that fits nothing.  Every entry below is a part you can
buy today, dimensioned from what its datasheet family typically says, and every
clearance ``maker_lib`` derives from these numbers comes out of
``printer.json``'s ``tolerances`` rather than out of a guess.

Every record therefore carries three honesty fields:

``source``
    Where the numbers come from -- always a *datasheet-typical* value for a
    family of parts, never a measurement of one specific unit.
``verify_against_your_part``
    One plain sentence naming what to measure before you print.  This is not
    boilerplate: clones of these parts vary by **+/- 0.3 mm** routinely and by
    more than that for anything with a moulded housing (coin-cell holders are
    the worst offenders in the table -- treat that entry as a shape, not a
    size).
``purchase_note``
    What to search for, and the trap in buying the wrong one.

Geometry conventions
--------------------
Each record's ``datum`` sentence says what ``Z = 0`` means for that component,
and ``parts`` lists the solids that make up its keep-out envelope in that frame
(``z0``/``z1`` in millimetres, negative below the datum).  ``maker_lib.envelope``
unions them; ``maker_lib.cutout`` builds the negative from the ``cutout``
recipe, growing each stage by the named ``printer.tolerances`` entry.

Units are millimetres, volts, milliamps, grams-force and millimetre-squared
throughout, and every key says which.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .errors import ScriptError


class ComponentError(ScriptError):
    """An unknown component, or a component asked for something it does not have.

    A :class:`~service.errors.ScriptError`, so it is an HTTP 400: the caller can
    fix it by naming a different part.  The message always lists what *is*
    available rather than only saying no.
    """


#: Categories the table is grouped into.
CATEGORIES: Tuple[str, ...] = ("switch", "power", "light", "fastener", "magnet")

#: The blanket accuracy claim for this table.  Quoted in every plan.
CLONE_TOLERANCE_MM = 0.3

#: LED forward voltages by colour, in volts -- the number the resistor maths
#: turns on.  Datasheet-typical for ordinary 20 mA indicator LEDs; the band is
#: what you should expect across a bag of them.
LED_FORWARD_VOLTAGE: Dict[str, Dict[str, Any]] = {
    "red": {"vf": 2.0, "band": (1.8, 2.2), "note": "the classic low-forward-voltage LED"},
    "orange": {"vf": 2.0, "band": (1.9, 2.2), "note": "behaves like red"},
    "amber": {"vf": 2.1, "band": (1.9, 2.4), "note": "behaves like red"},
    "yellow": {"vf": 2.1, "band": (1.9, 2.4), "note": "behaves like red"},
    "green": {"vf": 3.0, "band": (2.8, 3.4), "note": (
        "modern InGaN 'pure' green. The old yellow-green GaP part is 2.2 V -- if "
        "your green LED lights on a single 1.5 V cell in series with nothing, it "
        "is the old kind"
    )},
    "blue": {"vf": 3.0, "band": (2.8, 3.4), "note": "InGaN, same die family as white"},
    "white": {"vf": 3.0, "band": (2.8, 3.4), "note": (
        "a blue die under phosphor, so it has blue's forward voltage -- which is "
        "why a white LED runs straight off a 3 V coin cell with no resistor"
    )},
    "warm_white": {"vf": 3.0, "band": (2.8, 3.4), "note": "same die as white"},
    "uv": {"vf": 3.4, "band": (3.2, 3.8), "note": "needs more than a 3 V cell can give"},
    "infrared": {"vf": 1.4, "band": (1.2, 1.6), "note": "invisible; you cannot eyeball it working"},
}

#: The colour assumed when a script names an LED but not a colour.
DEFAULT_LED_COLOR = "white"


def _cyl(d: float, z0: float, z1: float, role: str, x: float = 0.0, y: float = 0.0) -> Dict[str, Any]:
    return {"shape": "cylinder", "diameter_mm": d, "z0_mm": z0, "z1_mm": z1,
            "x_mm": x, "y_mm": y, "role": role}


def _box(l: float, w: float, z0: float, z1: float, role: str,
         x: float = 0.0, y: float = 0.0) -> Dict[str, Any]:
    return {"shape": "box", "length_mm": l, "width_mm": w, "z0_mm": z0, "z1_mm": z1,
            "x_mm": x, "y_mm": y, "role": role}


def _stage(shape: str, z0: float, z1: float, fit: Optional[str], note: str,
           **dims: float) -> Dict[str, Any]:
    """One stage of a cutout: a solid grown by a named printer tolerance."""
    return {"shape": shape, "z0_mm": z0, "z1_mm": z1, "fit": fit, "note": note, **dims}


# ==========================================================================
# Switches
# ==========================================================================

_TACTILE_BODY_L = 6.0
_TACTILE_BODY_W = 6.0
_TACTILE_BUTTON_D = 3.5
#: Pin grid of a through-hole 6x6 tactile switch: 4.5 mm one way, 6.5 the other.
_TACTILE_PIN_X = 3.25
_TACTILE_PIN_Y = 2.25


def _tactile(name: str, body_h: float, overall_h: float, stroke: float,
             max_overtravel: float, force_gf: float, force_band: Tuple[float, float],
             latching: bool, life_cycles: int, summary: str,
             purchase: str) -> Dict[str, Any]:
    """One 6x6 tactile switch.  They differ only in height and in the mechanism."""
    button_h = overall_h - body_h
    return {
        "name": name,
        "category": "switch",
        "kind": "tactile_6x6",
        "summary": summary,
        "datum": (
            "Z = 0 is the switch's seating face (the flat underside of the black "
            "body -- where a PCB or a printed shelf holds it). The button faces +Z; "
            "the four legs stick out below."
        ),
        "body_length_mm": _TACTILE_BODY_L,
        "body_width_mm": _TACTILE_BODY_W,
        "body_height_mm": body_h,
        "overall_height_mm": overall_h,
        "button_diameter_mm": _TACTILE_BUTTON_D,
        "button_protrusion_mm": round(button_h, 3),
        "actuation": {
            "stroke_mm": stroke,
            "max_overtravel_mm": max_overtravel,
            "force_gf": force_gf,
            "force_band_gf": force_band,
            "latching": latching,
            "life_cycles": life_cycles,
            "note": (
                "push-on / push-off: one press latches it down and it STAYS down "
                f"{stroke:g} mm; the next press releases it and the spring returns "
                "it. There is no return spring holding the button up while it is "
                "latched, so anything riding on the button rides down with it."
                if latching else
                "momentary: it conducts only while held, and the spring returns "
                "the button the instant you let go."
            ),
        },
        "leads": {
            "count": 4,
            "kind": "through-hole",
            "pitch_x_mm": 2.0 * _TACTILE_PIN_X,
            "pitch_y_mm": 2.0 * _TACTILE_PIN_Y,
            "length_mm": 3.5,
            "note": (
                "Two diagonally opposite legs are one pole; the other two are the "
                "second. Legs on the SAME side are already joined inside -- if the "
                "switch seems permanently on, you soldered to a joined pair. Test "
                "with a multimeter's continuity beep before you solder anything."
            ),
        },
        "electrical": {
            "max_voltage_v": 12.0,
            "max_current_ma": 50.0,
            "note": "a signal switch, not a power switch: 12 V 50 mA is the family rating",
        },
        "parts": [
            _box(_TACTILE_BODY_L, _TACTILE_BODY_W, 0.0, body_h, "body"),
            _cyl(_TACTILE_BUTTON_D, body_h, overall_h, "actuator"),
            _box(1.0, 0.6, -3.5, 0.0, "lead", x=+_TACTILE_PIN_X, y=+_TACTILE_PIN_Y),
            _box(1.0, 0.6, -3.5, 0.0, "lead", x=-_TACTILE_PIN_X, y=+_TACTILE_PIN_Y),
            _box(1.0, 0.6, -3.5, 0.0, "lead", x=+_TACTILE_PIN_X, y=-_TACTILE_PIN_Y),
            _box(1.0, 0.6, -3.5, 0.0, "lead", x=-_TACTILE_PIN_X, y=-_TACTILE_PIN_Y),
        ],
        "cutout": {
            "style": "pocket",
            "datum_note": (
                "Subtract at the face the switch drops into; the pocket bores "
                "DOWN (-Z) and the button faces back up out of it."
            ),
            "stages": [
                _stage("box", -body_h, 0.0, "slide_fit",
                       "the black body, on a slide fit so it drops in without force",
                       length_mm=_TACTILE_BODY_L, width_mm=_TACTILE_BODY_W),
                _stage("box", -(body_h + 3.5), -body_h, "loose_fit",
                       "relief for the four legs; they must not be bent by the print",
                       length_mm=2.0 * _TACTILE_PIN_X + 2.0,
                       width_mm=2.0 * _TACTILE_PIN_Y + 2.0),
            ],
        },
        "mount_styles": ("shelf", "pocket_boss"),
        "source": (
            "datasheet-typical for the through-hole 6x6 mm tactile family "
            "(Alps SKHH / Omron B3F and the countless clones sold as "
            f"'6x6x{overall_h:g} tact switch')"
        ),
        "verify_against_your_part": (
            f"Measure the overall height with calipers: the 6x6 family is sold as "
            f"4.3 / 5 / 7.3 / 9.5 mm and the seller's photo is frequently of a "
            f"different one. Check the body height ({body_h:g} mm here) separately "
            "-- it is what sets how deep your pocket must be -- and press the "
            "button against a ruler to see the travel for yourself."
        ),
        "purchase_note": purchase,
    }


TACTILE_6X6_H43 = _tactile(
    "tactile_6x6_h43", body_h=3.5, overall_h=4.3, stroke=0.25, max_overtravel=0.20,
    force_gf=160.0, force_band=(100.0, 260.0), latching=False, life_cycles=100_000,
    summary="6x6 mm momentary tactile switch, 4.3 mm tall -- the standard board button",
    purchase="Search '6x6x4.3 tact switch'. The cheapest option in the table and the "
             "one to prototype with, but its 0.25 mm travel is far too little for a "
             "finger to feel through a plunger.",
)

TACTILE_6X6_H73 = _tactile(
    "tactile_6x6_h73", body_h=3.5, overall_h=7.3, stroke=0.25, max_overtravel=0.20,
    force_gf=160.0, force_band=(100.0, 260.0), latching=False, life_cycles=100_000,
    summary="6x6 mm momentary tactile switch, 7.3 mm tall -- same switch, longer button",
    purchase="Search '6x6x7.3 tact switch'. Identical mechanism to the 4.3; the extra "
             "3 mm is button, not travel, so it reaches through a thicker wall.",
)

TACTILE_6X6_H95 = _tactile(
    "tactile_6x6_h95", body_h=3.5, overall_h=9.5, stroke=0.25, max_overtravel=0.20,
    force_gf=160.0, force_band=(100.0, 260.0), latching=False, life_cycles=100_000,
    summary="6x6 mm momentary tactile switch, 9.5 mm tall -- the long-button variant",
    purchase="Search '6x6x9.5 tact switch'. The tall button often reaches a panel "
             "directly, which saves you a plunger entirely if you do not need a cap.",
)

TACTILE_6X6_LATCHING = _tactile(
    "tactile_6x6_latching", body_h=5.0, overall_h=7.3, stroke=1.5, max_overtravel=0.30,
    force_gf=250.0, force_band=(180.0, 350.0), latching=True, life_cycles=30_000,
    summary=(
        "6x6 mm SELF-LOCKING (push-on / push-off) tactile switch -- press once for "
        "on, again for off. The switch behind a push-the-figure-to-light-it toy."
    ),
    purchase=(
        "Search '6x6 self-locking tact switch' or '6x6 latching push button'. This "
        "is NOT the same part as an ordinary 6x6 tactile and the listings look "
        "identical -- the words to look for are self-locking, self-lock, latching "
        "or 'push on push off'. Buy a strip of ten; the mechanism is the part of "
        "this design most likely to arrive wrong."
    ),
)

PUSH_LATCHING_12MM = {
    "name": "push_latching_12mm",
    "category": "switch",
    "kind": "panel_push_latching",
    "summary": (
        "12 mm panel-mount latching push button (KD2-22 / PBS-110 style) -- the "
        "big satisfying click, with a knurled nut that clamps it to a wall"
    ),
    "datum": (
        "Z = 0 is the OUTSIDE face of the panel it clamps to. The bezel and the "
        "cap stand above it; the thread, the nut and the whole body hang below."
    ),
    "panel_hole_diameter_mm": 12.2,
    "thread_diameter_mm": 11.9,
    "thread_length_mm": 8.0,
    "bezel_diameter_mm": 14.5,
    "bezel_height_mm": 1.5,
    "cap_diameter_mm": 13.0,
    "cap_height_mm": 6.5,
    "body_diameter_mm": 15.0,
    "behind_panel_depth_mm": 25.0,
    "panel_thickness_min_mm": 1.0,
    "panel_thickness_max_mm": 4.0,
    "actuation": {
        "stroke_mm": 3.0,
        "max_overtravel_mm": 0.5,
        "force_gf": 500.0,
        "force_band_gf": (350.0, 700.0),
        "latching": True,
        "life_cycles": 10_000,
        "note": (
            "push-on / push-off with a mechanical latch you can hear. Latched, the "
            "cap sits about 3 mm lower and stays there."
        ),
    },
    "leads": {
        "count": 4,
        "kind": "solder lug",
        "length_mm": 4.0,
        "note": (
            "Usually DPDT: four lugs in two pairs. Find the pair that beeps only "
            "when the button is latched IN and use that pair; ignore the other two."
        ),
    },
    "electrical": {
        "max_voltage_v": 250.0,
        "max_current_ma": 1500.0,
        "note": (
            "rated for mains in its own datasheet. Do not wire mains. Nothing in "
            "this library, and nothing you print in PLA, is a mains enclosure."
        ),
    },
    "parts": [
        _cyl(13.0, 1.5, 8.0, "actuator"),
        _cyl(14.5, 0.0, 1.5, "bezel"),
        _cyl(11.9, -8.0, 0.0, "thread"),
        _cyl(15.0, -21.0, -8.0, "body"),
        _cyl(10.0, -25.0, -21.0, "lead"),
    ],
    "cutout": {
        "style": "panel_hole",
        "datum_note": (
            "Subtract at the OUTSIDE face of the panel; the hole bores DOWN (-Z) "
            "straight through it. The switch then goes in from behind and its nut "
            "does up on the outside."
        ),
        "stages": [
            _stage("cylinder", -4.0, 0.0, "slide_fit",
                   "the panel hole itself, on a slide fit over the threaded neck",
                   diameter_mm=11.9),
        ],
        "through": True,
        "panel_hole_floor_mm": 12.2,
    },
    "mount_styles": (),
    "source": (
        "datasheet-typical for the 12 mm KD2-22 / PBS-110 / R13-507 panel push "
        "family sold for project boxes"
    ),
    "verify_against_your_part": (
        "Measure the threaded neck's LENGTH, not just its diameter: it is what "
        "decides how thick your printed wall may be, and 8 mm is only typical. "
        "Check whether your switch has an anti-rotation flat on the thread -- if it "
        "does, the panel hole needs a matching flat or the switch spins when you "
        "tighten the nut. Count the lugs (2 or 4) before you design the wire route."
    ),
    "purchase_note": (
        "Search 'KD2-22 12mm latching push button' and confirm the listing says "
        "self-locking / latching rather than momentary. Buy the one WITH the nut "
        "and the toothed washer; a nut alone will work loose."
    ),
}

SLIDE_SWITCH_SK12 = {
    "name": "slide_switch_sk12",
    "category": "switch",
    "kind": "slide_spdt",
    "summary": (
        "SPDT miniature slide switch (SK-12D07 / SS-12D00 family) -- the tiny "
        "on/off slider. No plunger needed and it cannot be pressed by accident."
    ),
    "datum": (
        "Z = 0 is the switch's seating face. The slider knob faces +Z; the three "
        "pins stick out below."
    ),
    "body_length_mm": 7.0,
    "body_width_mm": 3.5,
    "body_height_mm": 3.5,
    "actuator_length_mm": 2.0,
    "actuator_width_mm": 1.5,
    "actuator_height_mm": 2.0,
    "overall_height_mm": 5.5,
    "lug_span_mm": 9.8,
    "lug_hole_diameter_mm": 2.0,
    "lug_pitch_mm": 8.0,
    "actuation": {
        "stroke_mm": 2.5,
        "max_overtravel_mm": 0.0,
        "force_gf": 300.0,
        "force_band_gf": (150.0, 500.0),
        "latching": True,
        "life_cycles": 5_000,
        "note": (
            "a slider, not a push: it moves SIDEWAYS 2.5 mm and stays where you put "
            "it. Nothing in maker_lib's plunger applies to it -- give it a slot in "
            "the wall wide enough for the knob to travel, and no plunger at all."
        ),
    },
    "leads": {
        "count": 3,
        "kind": "through-hole",
        "pitch_mm": 2.54,
        "length_mm": 3.5,
        "note": (
            "The MIDDLE pin is the common one. Wire your battery to the middle pin "
            "and the LED to either outer pin; the other outer pin does nothing."
        ),
    },
    "electrical": {
        "max_voltage_v": 50.0,
        "max_current_ma": 300.0,
        "note": "50 V 0.3 A -- plenty for an LED, nowhere near a motor",
    },
    "parts": [
        _box(7.0, 3.5, 0.0, 3.5, "body"),
        _box(2.0, 1.5, 3.5, 5.5, "actuator"),
        _box(9.8, 3.5, 0.0, 0.3, "flange"),
        _box(1.0, 0.6, -3.5, 0.0, "lead", x=-2.54),
        _box(1.0, 0.6, -3.5, 0.0, "lead"),
        _box(1.0, 0.6, -3.5, 0.0, "lead", x=2.54),
    ],
    "cutout": {
        "style": "pocket",
        "datum_note": "Subtract at the face the switch drops into; bores DOWN (-Z).",
        "stages": [
            _stage("box", -3.5, 0.0, "slide_fit", "the body",
                   length_mm=7.0, width_mm=3.5),
            _stage("box", -7.0, -3.5, "loose_fit", "pin relief",
                   length_mm=8.0, width_mm=3.0),
        ],
    },
    "mount_styles": ("shelf",),
    "source": "datasheet-typical for the SS-12D00 / SK-12D07 SPDT slide family",
    "verify_against_your_part": (
        "This family is sold in a dozen near-identical variants with different "
        "actuator heights and mounting lugs. Measure the body and the knob height, "
        "and check whether yours has lugs at all -- the ones without are 3.5 mm "
        "narrower and will rattle in a slot cut for the lugged kind."
    ),
    "purchase_note": (
        "Search 'SS-12D00 slide switch' or 'SK12D07'. The cheapest reliable on/off "
        "in this table, and the right answer whenever the push mechanic is "
        "decoration rather than the point."
    ),
}


# ==========================================================================
# Power
# ==========================================================================

CR2032_CELL = {
    "name": "cr2032_cell",
    "category": "power",
    "kind": "coin_cell",
    "summary": "CR2032 3 V lithium coin cell -- 20 mm across, the size of a coat button",
    "datum": (
        "Z = 0 is the cell's NEGATIVE face (the small flat bottom). The positive "
        "face -- the big one with the writing -- is at +Z."
    ),
    "diameter_mm": 20.0,
    "thickness_mm": 3.2,
    "diameter_band_mm": (19.7, 20.0),
    "thickness_band_mm": (3.0, 3.2),
    "mass_g": 3.0,
    "electrical": {
        "nominal_voltage_v": 3.0,
        "fresh_voltage_v": 3.2,
        "end_of_life_voltage_v": 2.0,
        "capacity_mah": 220.0,
        "capacity_band_mah": (210.0, 240.0),
        "recommended_current_ma": 3.0,
        "internal_resistance_ohm": 30.0,
        "note": (
            "The datasheet's standard drain is 0.2 mA. 3 mA is the practical "
            "ceiling for a cell you want to last; 20 mA works for tens of minutes "
            "and ruins the cell. Its own 10-40 ohm internal resistance is what "
            "limits an LED wired straight across it -- that is not a design, it is "
            "a coincidence that happens to work."
        ),
    },
    "polarity": (
        "The WIDE flat face with the writing on it is +. The small face on the "
        "other side, the one with the rim around it, is -. Get this backwards and "
        "the LED simply does not light; nothing is damaged."
    ),
    "parts": [_cyl(20.0, 0.0, 3.2, "body")],
    "cutout": {
        "style": "pocket",
        "datum_note": "Subtract at the face the cell drops onto; bores DOWN (-Z).",
        "stages": [
            _stage("cylinder", -3.2, 0.0, "slide_fit", "the cell", diameter_mm=20.0),
        ],
    },
    "mount_styles": (),
    "source": "IEC CR2032 standard dimensions (all makers hold these)",
    "verify_against_your_part": (
        "This is the one entry in the table you do not need to measure: CR2032 is "
        "a standard and every brand holds it. Do check the date on the packet -- "
        "coin cells self-discharge on the shelf."
    ),
    "purchase_note": (
        "Buy a named brand from a shop with stock turnover. The bulk no-name cells "
        "are frequently years old and start at half capacity. Never buy a CR2032 "
        "with solder tabs unless you are soldering it -- soldering a bare coin "
        "cell can vent it."
    ),
}

CR2032_HOLDER = {
    "name": "cr2032_holder",
    "category": "power",
    "kind": "coin_cell_holder",
    "summary": (
        "Through-hole CR2032 holder -- the black plastic clip that takes the cell "
        "sideways and gives you two solderable pins"
    ),
    "datum": (
        "Z = 0 is the holder's seating face (its flat underside). The body is at "
        "+Z; the two pins stick out below."
    ),
    "body_length_mm": 26.0,
    "body_width_mm": 24.0,
    "body_height_mm": 6.0,
    "cell_bore_diameter_mm": 20.5,
    "insertion": "the cell slides in from one side, so leave that side clear",
    "leads": {
        "count": 2,
        "kind": "through-hole",
        "pitch_mm": 20.0,
        "diameter_mm": 1.0,
        "length_mm": 3.0,
        "note": (
            "The pin joined to the wide bottom plate is -; the pin on the little "
            "sprung finger that presses on the cell's top face is +. If you cannot "
            "tell, put a cell in and touch a multimeter to the pins."
        ),
    },
    "mount_hole_diameter_mm": 2.2,
    "mount_hole_pitch_mm": 20.0,
    "electrical": {
        "nominal_voltage_v": 3.0,
        "note": "just a holder -- the cell's numbers are the circuit's numbers",
    },
    "parts": [
        _box(26.0, 24.0, 0.0, 6.0, "body"),
        _cyl(1.4, -3.0, 0.0, "lead", x=-10.0),
        _cyl(1.4, -3.0, 0.0, "lead", x=10.0),
    ],
    "cutout": {
        "style": "pocket",
        "datum_note": "Subtract at the shelf the holder sits on; bores DOWN (-Z).",
        "stages": [
            _stage("box", -6.0, 0.0, "slide_fit", "the holder body, dropped into a seat",
                   length_mm=26.0, width_mm=24.0),
            _stage("box", -9.0, -6.0, "loose_fit", "pin relief",
                   length_mm=22.0, width_mm=3.0),
        ],
    },
    "mount_styles": ("screw", "rib"),
    "source": (
        "envelope typical of the generic through-hole CR2032 holder (BS-2032 / "
        "Keystone 1058 class)"
    ),
    "verify_against_your_part": (
        "MEASURE THIS ONE. Coin-cell holders are the loosest entry in the table: "
        "the same search term returns parts from 20 to 28 mm long, with and "
        "without mounting ears, with the cell entering from the side or from the "
        "top. Put yours on the bed of your calipers before you model a seat for "
        "it, and check which SIDE the cell goes in -- a seat with walls all round "
        "makes the cell unchangeable."
    ),
    "purchase_note": (
        "Search 'CR2032 holder through hole'. Prefer one with mounting ears: two "
        "M2 screws into printed bosses is a far better fix than glue, which "
        "creeps and lets the holder tilt until the cell loses contact."
    ),
}

AAA_PAIR_BOX = {
    "name": "aaa_pair_box",
    "category": "power",
    "kind": "battery_box",
    "summary": (
        "2 x AAA battery box with flying leads -- 3 V like a coin cell, but with "
        "five times the capacity and no current limit worth worrying about"
    ),
    "datum": "Z = 0 is the box's flat back. The box is at +Z; the wires leave one end.",
    "body_length_mm": 52.0,
    "body_width_mm": 26.0,
    "body_height_mm": 14.0,
    "leads": {
        "count": 2,
        "kind": "flying lead",
        "length_mm": 150.0,
        "gauge_awg": 26,
        "note": "RED is +, BLACK is -. This is a convention, not a law: check it with a meter.",
    },
    "mount_hole_diameter_mm": 2.5,
    "mount_hole_pitch_mm": 44.0,
    "electrical": {
        "nominal_voltage_v": 3.0,
        "fresh_voltage_v": 3.2,
        "capacity_mah": 1000.0,
        "recommended_current_ma": 100.0,
        "internal_resistance_ohm": 0.5,
        "note": (
            "Two alkaline AAAs give the same 3 V as a coin cell with about five "
            "times the run time, and unlike a coin cell they WILL deliver 20 mA "
            "all day -- which means the resistor stops being optional."
        ),
    },
    "polarity": "the red wire is +, the black wire is -",
    "parts": [_box(52.0, 26.0, 0.0, 14.0, "body")],
    "cutout": {
        "style": "pocket",
        "datum_note": "Subtract at the floor the box sits on; bores DOWN (-Z).",
        "stages": [
            _stage("box", -14.0, 0.0, "loose_fit",
                   "the box, on a loose fit -- moulded battery boxes are not precise",
                   length_mm=52.0, width_mm=26.0),
        ],
    },
    "mount_styles": ("screw",),
    "source": "envelope typical of the generic 2xAAA closed box with 150 mm leads",
    "verify_against_your_part": (
        "Battery boxes vary more than anything else here -- with a lid, with a "
        "switch, with a JST plug, side by side or end to end. Measure yours, and "
        "check whether the lid needs room to slide off."
    ),
    "purchase_note": (
        "Search '2xAAA battery holder with wires'. Get the version with a switch "
        "and a lid if the design has no other way in; get the plain one if your "
        "own battery door is doing that job."
    ),
}


# ==========================================================================
# Light
# ==========================================================================


def _led(name: str, dome_d: float, flange_d: float, flange_h: float,
         overall_h: float, max_current_ma: float, viewing_angle_deg: float,
         summary: str, purchase: str, verify_extra: str = "") -> Dict[str, Any]:
    """One through-hole LED.  They differ only in size and in current."""
    above = overall_h - flange_h
    return {
        "name": name,
        "category": "light",
        "kind": "led_tht",
        "summary": summary,
        "datum": (
            "Z = 0 is the top of the flange -- the shoulder that stops the LED "
            "falling out through its hole, so it is the INSIDE face of the wall you "
            "press it into. The lens is at +Z (outside); the flange and the two "
            "legs are at -Z (inside)."
        ),
        "dome_diameter_mm": dome_d,
        "flange_diameter_mm": flange_d,
        "flange_height_mm": flange_h,
        "overall_height_mm": overall_h,
        "above_flange_mm": round(above, 3),
        "viewing_angle_deg": viewing_angle_deg,
        "leads": {
            "count": 2,
            "kind": "through-hole",
            "pitch_mm": 2.54,
            "length_mm": 25.4,
            "section_mm": 0.5,
            "note": (
                "The LONGER leg is + (the anode). The shorter leg is - (the "
                "cathode), and the flange has a FLAT filed on it beside that leg so "
                "you can still tell after you trim them. Trim the long one last, or "
                "trim neither until it is working."
            ),
        },
        "electrical": {
            "max_current_ma": max_current_ma,
            "nominal_current_ma": 20.0,
            "colors": sorted(LED_FORWARD_VOLTAGE),
            "default_color": DEFAULT_LED_COLOR,
            "note": (
                "An LED is a diode, not a bulb: it has no useful resistance of its "
                "own, so whatever the supply can push through it, it takes -- until "
                "it dies. Something has to limit the current. A resistor is the "
                "usual something; a coin cell's own internal resistance is the "
                "exception this library will tell you about when it applies."
            ),
        },
        "polarity": "longer leg = + (anode); shorter leg beside the flat = - (cathode)",
        "parts": [
            _cyl(dome_d, 0.0, above, "lens"),
            _cyl(flange_d, -flange_h, 0.0, "flange"),
            _box(0.6, 0.6, -(flange_h + 25.4), -flange_h, "lead", x=-1.27),
            _box(0.6, 0.6, -(flange_h + 25.4), -flange_h, "lead", x=1.27),
        ],
        "cutout": {
            "style": "bore",
            "needs_depth": True,
            "datum_note": (
                "Subtract at the OUTSIDE face of the wall the LED shines through; "
                "the bore goes DOWN (-Z) into the material. Pass depth= the "
                "thickness the lens must pass through -- wall plus any collar you "
                "unioned on the inside."
            ),
            "stages": [
                _stage("cylinder", None, 0.0, "press_fit",
                       "the lens bore, on a press fit so the LED grips",
                       diameter_mm=dome_d),
                _stage("cylinder", None, None, "slide_fit",
                       "the flange counterbore -- the shoulder the LED seats against",
                       diameter_mm=flange_d),
                _stage("cylinder", None, None, "loose_fit",
                       "relief so the two legs are not crushed against solid plastic",
                       diameter_mm=2.54 + 1.4),
            ],
        },
        "mount_styles": ("collar",),
        "grip_note": (
            f"A {dome_d:g} mm LED wants about 3 mm of bore around it to stay put. "
            "A minimum-wall shell gives it 0.8 mm, which is a press fit in name "
            f"only -- union mount('{name}', style='collar') onto the inside face "
            "and bore through both."
        ),
        "source": (
            f"datasheet-typical for the {dome_d:g} mm through-hole indicator LED "
            "family (Kingbright / Everlight T-1 class)"
        ),
        "verify_against_your_part": (
            f"Measure the FLANGE diameter ({flange_d:g} mm here), not the lens: it "
            "is the number that decides whether the LED seats or falls through, and "
            "it is the number clones get wrong. Some diffused LEDs have no flange at "
            "all. Check the leg lengths differ before you trim anything -- once both "
            "legs are short the only polarity marker left is the flat."
            + (" " + verify_extra if verify_extra else "")
        ),
        "purchase_note": purchase,
    }


LED_3MM = _led(
    "led_3mm", dome_d=3.0, flange_d=3.4, flange_h=0.9, overall_h=5.3,
    max_current_ma=20.0, viewing_angle_deg=30.0,
    summary="3 mm through-hole LED -- the small indicator",
    purchase="Search '3mm LED assorted'. Buy DIFFUSED rather than water-clear for "
             "anything that has to glow through a translucent part: a clear LED is "
             "a bright point with dark around it, a diffused one lights the whole "
             "cavity.",
)

LED_5MM = _led(
    "led_5mm", dome_d=5.0, flange_d=5.8, flange_h=1.0, overall_h=8.6,
    max_current_ma=20.0, viewing_angle_deg=30.0,
    summary="5 mm through-hole LED -- the default, and the one to design around",
    purchase="Search '5mm diffused LED'. Diffused, always, for a figure that glows: "
             "the phosphor point of a clear white LED shows straight through a "
             "translucent print as a hot spot.",
)

LED_10MM = _led(
    "led_10mm", dome_d=10.0, flange_d=11.0, flange_h=1.2, overall_h=13.5,
    max_current_ma=20.0, viewing_angle_deg=25.0,
    summary="10 mm through-hole LED -- the big one, for filling a large diffuser",
    purchase="Search '10mm diffused LED'. Worth it only when the part it lights is "
             "big enough that a 5 mm LED reads as a bright dot inside it.",
    verify_extra=(
        "Lead spacing especially: 2.54 mm is typical but 10 mm LEDs are the size "
        "where makers stop bothering, and a wider pair will not drop into a hole "
        "drilled on 2.54."
    ),
)


# ==========================================================================
# Fasteners and magnets
# ==========================================================================

M3_SCREW = {
    "name": "m3_screw",
    "category": "fastener",
    "kind": "machine_screw",
    "summary": "M3 machine screw -- the default fastener for printed assemblies",
    "datum": (
        "Z = 0 is the under-head bearing face (where the head lands). The head is "
        "at +Z; the thread hangs below."
    ),
    "thread_diameter_mm": 3.0,
    "thread_pitch_mm": 0.5,
    "heads": {
        "pan": {"diameter_mm": 6.0, "height_mm": 2.4, "standard": "DIN 7985"},
        "socket_cap": {"diameter_mm": 5.5, "height_mm": 3.0, "standard": "DIN 912"},
        "countersunk": {"diameter_mm": 6.0, "height_mm": 1.7, "angle_deg": 90.0,
                        "standard": "DIN 965"},
    },
    "clearance_hole_close_mm": 3.2,
    "clearance_hole_normal_mm": 3.4,
    "thread_forming_hole_mm": 2.4,
    "common_lengths_mm": (6.0, 8.0, 10.0, 12.0, 16.0, 20.0, 25.0),
    "parts": [
        _cyl(6.0, 0.0, 2.4, "head"),
        _cyl(3.0, -10.0, 0.0, "thread"),
    ],
    "cutout": {
        "style": "bore",
        "needs_depth": True,
        "datum_note": (
            "Subtract at the face the head lands on; bores DOWN (-Z). Pass depth= "
            "how far the screw has to pass through."
        ),
        "stages": [
            _stage("cylinder", None, 0.0, None,
                   "the clearance hole -- 3.4 mm is a standard, not a printer "
                   "tolerance, so nothing is added to it",
                   diameter_mm=3.4),
        ],
    },
    "mount_styles": ("boss",),
    "source": "ISO 261 / DIN head standards",
    "verify_against_your_part": (
        "Check the head STYLE before you model a counterbore: a socket cap needs a "
        "5.5 mm round pocket, a countersunk screw needs a 90 degree cone, and they "
        "are not interchangeable. Measure the length -- screws are sold by shank "
        "length, except countersunk ones, which are sold overall."
    ),
    "purchase_note": (
        "Buy an M3 assortment box with nuts and washers. Prefer socket cap: a hex "
        "key cannot cam out and round off a printed boss the way a cross-head "
        "driver can."
    ),
}

HEAT_SET_M3 = {
    "name": "heat_set_m3",
    "category": "fastener",
    "kind": "heat_set_insert",
    "summary": (
        "M3 brass heat-set insert -- melted into a printed boss with a soldering "
        "iron, giving a real metal thread that survives being undone"
    ),
    "datum": "Z = 0 is the top of the boss (the insert's flush face). The insert is at -Z.",
    "thread": "M3 x 0.5",
    "length_mm": 5.7,
    "outer_diameter_max_mm": 4.6,
    "outer_diameter_nose_mm": 4.0,
    "hole_diameter_mm": 4.0,
    "hole_depth_mm": 6.2,
    "hole_source": (
        "the INSERT MAKER's number, not printer.tolerances. A heat-set hole is a "
        "deliberate interference: the brass melts its own way in and the displaced "
        "plastic is what grips it. Applying a printed fit clearance to this hole "
        "would make it a loose hole with a brass ring rattling in it."
    ),
    "min_boss_wall_mm": 1.5,
    "min_boss_diameter_mm": 7.0,
    "parts": [_cyl(4.6, -5.7, 0.0, "body")],
    "cutout": {
        "style": "bore",
        "datum_note": "Subtract at the top of the boss; bores DOWN (-Z).",
        "stages": [
            _stage("cylinder", -6.2, 0.0, None,
                   "the insert hole -- fixed by the insert maker, no printer "
                   "tolerance applied",
                   diameter_mm=4.0),
        ],
    },
    "mount_styles": ("boss",),
    "source": "Ruthex / CNC Kitchen M3 knurled heat-set insert dimensions",
    "verify_against_your_part": (
        "Read the packet for the RECOMMENDED HOLE DIAMETER and use that number, not "
        "this one: M3 inserts are sold in at least three lengths (3.0, 4.0, 5.7 mm) "
        "with different recommended holes, and a hole 0.4 mm out is the difference "
        "between an insert that grips and one that sinks straight through."
    ),
    "purchase_note": (
        "Search 'M3 heat set insert knurled'. Buy the matching iron tip too -- "
        "pressing an insert in with a bare conical tip sends it in crooked, and "
        "crooked is not fixable."
    ),
}


def _magnet(name: str, d: float, t: float, pull_kg: float) -> Dict[str, Any]:
    return {
        "name": name,
        "category": "magnet",
        "kind": "disc_magnet",
        "summary": f"{d:g} x {t:g} mm N35 neodymium disc magnet -- about {pull_kg:g} kg of pull",
        "datum": "Z = 0 is the pocket mouth; the magnet sits below it.",
        "diameter_mm": d,
        "thickness_mm": t,
        "tolerance_mm": 0.1,
        "pull_force_kg": pull_kg,
        "grade": "N35",
        "max_temperature_c": 80.0,
        "parts": [_cyl(d, 0.0, t, "body")],
        "cutout": {
            "style": "magnet_pocket",
            "datum_note": (
                "Handled by forge_lib.magnet_pocket -- the same sizing /segment's "
                "magnet joints use. Subtract at the face; bores DOWN (-Z)."
            ),
            "stages": [
                _stage("cylinder", -t, 0.0, "magnet_pocket_extra", "the magnet",
                       diameter_mm=d),
            ],
        },
        "mount_styles": (),
        "polarity": (
            "The two FLAT faces are the poles. Two magnets that snap together are "
            "correctly oriented; two that shove apart are one flip away from it. "
            "Check every pair BEFORE any glue goes anywhere near them."
        ),
        "source": "typical N35 sintered NdFeB disc, nickel plated",
        "verify_against_your_part": (
            "Magnets are held to about +/- 0.1 mm, so the pocket sizing is reliable "
            "-- but check the GRADE. N52 in the same size pulls roughly half again "
            "as hard, which is the difference between a door that opens and a door "
            "you cannot get a fingernail into."
        ),
        "purchase_note": (
            "Search 'N35 neodymium disc 6x3' with your size. Buy twice as many as "
            "you need; they chip, they jump out of your fingers into carpet, and "
            "they arrive stuck in a stack you will lose count of. Keep them away "
            "from a hot end -- above 80 C they lose their magnetism permanently."
        ),
    }


MAGNET_5X2 = _magnet("magnet_5x2", 5.0, 2.0, 0.5)
MAGNET_6X3 = _magnet("magnet_6x3", 6.0, 3.0, 0.9)
MAGNET_8X3 = _magnet("magnet_8x3", 8.0, 3.0, 1.6)
MAGNET_10X2 = _magnet("magnet_10x2", 10.0, 2.0, 1.7)


# ==========================================================================
# The table
# ==========================================================================

COMPONENTS: Dict[str, Dict[str, Any]] = {
    entry["name"]: entry
    for entry in (
        TACTILE_6X6_H43,
        TACTILE_6X6_H73,
        TACTILE_6X6_H95,
        TACTILE_6X6_LATCHING,
        PUSH_LATCHING_12MM,
        SLIDE_SWITCH_SK12,
        CR2032_CELL,
        CR2032_HOLDER,
        AAA_PAIR_BOX,
        LED_3MM,
        LED_5MM,
        LED_10MM,
        M3_SCREW,
        HEAT_SET_M3,
        MAGNET_5X2,
        MAGNET_6X3,
        MAGNET_8X3,
        MAGNET_10X2,
    )
}


def catalog(category: Optional[str] = None) -> List[str]:
    """Component names, optionally filtered to one :data:`CATEGORIES` entry."""
    if category is None:
        return sorted(COMPONENTS)
    key = str(category).strip().lower()
    if key not in CATEGORIES:
        raise ComponentError(
            f"unknown category {category!r}; the categories are "
            f"{', '.join(CATEGORIES)}"
        )
    return sorted(name for name, entry in COMPONENTS.items() if entry["category"] == key)


def component(name: Any) -> Dict[str, Any]:
    """One component record, deep-copied so a caller cannot edit the table.

    Accepts a name or a record (so helpers can take either without checking).
    """
    if isinstance(name, Mapping) and "name" in name and "category" in name:
        return copy.deepcopy(dict(name))
    if not isinstance(name, str):
        raise ComponentError(
            f"expected a component name from components.catalog(), got "
            f"{type(name).__name__}"
        )
    key = name.strip()
    if key not in COMPONENTS:
        near = [candidate for candidate in sorted(COMPONENTS) if key.lower() in candidate]
        hint = (
            f" Did you mean {', '.join(near)}?"
            if near
            else f" The catalog is: {', '.join(sorted(COMPONENTS))}."
        )
        raise ComponentError(f"no component named {name!r}.{hint}")
    return copy.deepcopy(COMPONENTS[key])


def require_category(name: Any, *categories: str) -> Dict[str, Any]:
    """Fetch a component and insist it is one of *categories*."""
    entry = component(name)
    if entry["category"] not in categories:
        wanted = " or ".join(categories)
        raise ComponentError(
            f"{entry['name']} is a {entry['category']}, and this needs a {wanted}. "
            f"The {wanted} components are: "
            + ", ".join(sorted(sum((catalog(one) for one in categories), [])))
        )
    return entry


def led_forward_voltage(color: Any = DEFAULT_LED_COLOR) -> Dict[str, Any]:
    """The forward-voltage record for one LED colour."""
    key = str(color).strip().lower().replace(" ", "_").replace("-", "_")
    if key not in LED_FORWARD_VOLTAGE:
        raise ComponentError(
            f"no forward voltage on file for LED colour {color!r}; the colours are "
            f"{', '.join(sorted(LED_FORWARD_VOLTAGE))}"
        )
    record = dict(LED_FORWARD_VOLTAGE[key])
    record["color"] = key
    return record


def extent(entry: Mapping[str, Any], roles: Optional[Tuple[str, ...]] = None) -> Dict[str, float]:
    """The bounding extent of a component's ``parts``, in its own datum frame."""
    parts = [
        part for part in entry.get("parts") or ()
        if roles is None or part.get("role") in roles
    ]
    if not parts:
        raise ComponentError(f"{entry.get('name')!r} has no parts to measure")
    xs: List[float] = []
    ys: List[float] = []
    zs: List[float] = []
    for part in parts:
        cx = float(part.get("x_mm", 0.0))
        cy = float(part.get("y_mm", 0.0))
        if part["shape"] == "cylinder":
            half_x = half_y = float(part["diameter_mm"]) / 2.0
        else:
            half_x = float(part["length_mm"]) / 2.0
            half_y = float(part["width_mm"]) / 2.0
        xs.extend((cx - half_x, cx + half_x))
        ys.extend((cy - half_y, cy + half_y))
        zs.extend((float(part["z0_mm"]), float(part["z1_mm"])))
    return {
        "length_mm": round(max(xs) - min(xs), 5),
        "width_mm": round(max(ys) - min(ys), 5),
        "height_mm": round(max(zs) - min(zs), 5),
        "z_min_mm": round(min(zs), 5),
        "z_max_mm": round(max(zs), 5),
    }


def honesty(entry: Mapping[str, Any]) -> Dict[str, Any]:
    """The three fields every plan repeats, so a reply never drops them."""
    return {
        "source": entry["source"],
        "verify_against_your_part": entry["verify_against_your_part"],
        "purchase_note": entry["purchase_note"],
        "clone_tolerance_mm": CLONE_TOLERANCE_MM,
    }


__all__ = [
    "AAA_PAIR_BOX",
    "CATEGORIES",
    "CLONE_TOLERANCE_MM",
    "COMPONENTS",
    "CR2032_CELL",
    "CR2032_HOLDER",
    "ComponentError",
    "DEFAULT_LED_COLOR",
    "HEAT_SET_M3",
    "LED_10MM",
    "LED_3MM",
    "LED_5MM",
    "LED_FORWARD_VOLTAGE",
    "M3_SCREW",
    "MAGNET_10X2",
    "MAGNET_5X2",
    "MAGNET_6X3",
    "MAGNET_8X3",
    "PUSH_LATCHING_12MM",
    "SLIDE_SWITCH_SK12",
    "TACTILE_6X6_H43",
    "TACTILE_6X6_H73",
    "TACTILE_6X6_H95",
    "TACTILE_6X6_LATCHING",
    "catalog",
    "component",
    "extent",
    "honesty",
    "led_forward_voltage",
    "require_category",
]
