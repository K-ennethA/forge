"""``wiring`` -- the circuit half of maker mode: what to solder, and why.

A printed housing with a perfect switch pocket and a perfect LED bore is still
not a lamp.  This module closes that gap the same way :mod:`service.casting`
closes it for silicone: **arithmetic that answers the number, then instructions
in words a beginner can follow.**

Two entry points:

:func:`circuit_plan`
    The numbers.  Supply voltage, LED forward voltage, the resistor that goes
    between them, the current that results, how long the cell lasts, and -- the
    answer people are most often surprised by -- whether a resistor is needed at
    all.

:func:`wiring_steps`
    The same plan as an ordered list of plain sentences: which leg is positive,
    what order the parts go in, how to insulate, and the one step that saves the
    most rework, which is *test it before you glue anything*.

:func:`diagram_svg` draws the loop as boxes and lines.  It is a string of SVG
built by string formatting -- no dependency, no renderer, nothing to install.

Nothing in here imports build123d.

The one idea worth carrying away
--------------------------------
An LED is a **diode**, not a bulb.  It has no useful resistance of its own, so
it does not "draw" a current -- it takes whatever the supply can push through
it, until it dies.  Something in the loop has to limit that current.  Usually
that something is a resistor you choose.  Occasionally -- a 3 V coin cell and a
white or blue LED -- the supply's own internal resistance does the job, which is
why LED throwies work, and why this module will sometimes tell you the resistor
is optional and then tell you what you give up by leaving it out.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import components as _components
from .errors import ScriptError


class CircuitError(ScriptError):
    """A circuit that cannot work -- an HTTP 400, because a parameter fixes it."""


#: The E12 preferred series, one decade.  Resistors are not sold in every value;
#: these twelve times a power of ten are what a beginner's assortment contains.
E12: Sequence[float] = (10, 12, 15, 18, 22, 27, 33, 39, 47, 56, 68, 82)

#: The E24 series, for callers who have a fuller assortment.
E24: Sequence[float] = (
    10, 11, 12, 13, 15, 16, 18, 20, 22, 24, 27, 30,
    33, 36, 39, 43, 47, 51, 56, 62, 68, 75, 82, 91,
)

SERIES = {"E12": E12, "E24": E24}

#: Headroom under which a coin cell's own internal resistance is doing enough of
#: the limiting that a resistor becomes a judgement call rather than a
#: requirement.  Above it the resistor is not optional on any supply.
COIN_CELL_OPTIONAL_HEADROOM_V = 1.2

#: Supplies whose internal resistance is high enough to limit an LED by itself.
_CURRENT_LIMITED_KINDS = ("coin_cell",)

#: Standard resistor power ratings, watts, smallest first.
_POWER_RATINGS = (0.125, 0.25, 0.5, 1.0, 2.0)

#: Fraction of a cell's nameplate capacity you actually get at a real load.
#: Coin cells in particular do not deliver their rated 220 mAh at 20 mA.
_CAPACITY_DERATE = 0.7


def _positive(value: Any, label: str, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CircuitError(f"{label} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise CircuitError(f"{label} must be a finite number, got {value!r}")
    if number < 0.0 or (number == 0.0 and not allow_zero):
        raise CircuitError(
            f"{label} must be {'zero or more' if allow_zero else 'greater than zero'}, "
            f"got {number:g}"
        )
    return number


def preferred_resistor(ohms: float, series: str = "E12") -> float:
    """The smallest *series* value at or above *ohms*.

    Rounding **up** is the safe direction: a bigger resistor means less current,
    which means a dimmer LED rather than a dead one.
    """
    target = _positive(ohms, "ohms", allow_zero=True)
    if target <= 0.0:
        return 0.0
    name = str(series).strip().upper()
    if name not in SERIES:
        raise CircuitError(
            f"resistor series must be one of {', '.join(sorted(SERIES))}, got {series!r}"
        )
    values = SERIES[name]
    decade = 10.0 ** math.floor(math.log10(target))
    for scale in (decade / 10.0, decade, decade * 10.0, decade * 100.0):
        for value in values:
            candidate = value * scale
            if candidate >= target - 1e-9:
                return round(candidate, 6)
    return round(values[0] * decade * 1000.0, 6)


def power_rating_for(watts: float) -> float:
    """The smallest standard resistor rating with at least 2x headroom over *watts*."""
    needed = max(0.0, float(watts)) * 2.0
    for rating in _POWER_RATINGS:
        if rating >= needed:
            return rating
    return _POWER_RATINGS[-1]


def _supply(cell: Any, cells: int, supply_v: Optional[float]) -> Dict[str, Any]:
    """Resolve the power source into volts, capacity and an internal resistance."""
    if cell is None:
        if supply_v is None:
            raise CircuitError(
                "circuit_plan needs either a cell= from components.catalog('power') "
                "or an explicit supply_v="
            )
        volts = _positive(supply_v, "supply_v")
        return {
            "name": None,
            "kind": "bench",
            "cells": 1,
            "voltage_v": volts,
            "cell_voltage_v": volts,
            "fresh_voltage_v": volts,
            "capacity_mah": None,
            "recommended_current_ma": None,
            "internal_resistance_ohm": 0.0,
            "current_limited": False,
            "polarity": "whatever your supply's + and - terminals are",
        }

    entry = _components.require_category(cell, "power")
    count = int(cells)
    if count < 1:
        raise CircuitError(f"cells must be at least 1, got {cells!r}")
    electrical = entry["electrical"]
    one_cell = float(electrical["nominal_voltage_v"])
    fresh_cell = float(electrical.get("fresh_voltage_v") or one_cell)
    volts = one_cell * count if supply_v is None else _positive(supply_v, "supply_v")
    resistance = float(electrical.get("internal_resistance_ohm") or 0.0) * count
    capacity = electrical.get("capacity_mah")
    return {
        "name": entry["name"],
        "kind": entry["kind"],
        "cells": count,
        "voltage_v": round(volts, 4),
        "cell_voltage_v": one_cell,
        "fresh_voltage_v": round(fresh_cell * count, 4),
        "capacity_mah": float(capacity) if capacity else None,
        "recommended_current_ma": (
            float(electrical["recommended_current_ma"])
            if electrical.get("recommended_current_ma")
            else None
        ),
        "internal_resistance_ohm": round(resistance, 4),
        "current_limited": entry["kind"] in _CURRENT_LIMITED_KINDS,
        "polarity": entry.get("polarity") or "check with a multimeter",
        "note": electrical.get("note"),
    }


def _runtime_hours(capacity_mah: Optional[float], current_ma: float) -> Optional[float]:
    if not capacity_mah or current_ma <= 0.0:
        return None
    return round(capacity_mah * _CAPACITY_DERATE / current_ma, 2)


def circuit_plan(
    led: Any = "led_5mm",
    *,
    color: Optional[str] = None,
    cell: Any = "cr2032_cell",
    cells: int = 1,
    switch: Any = "tactile_6x6_latching",
    current_ma: Optional[float] = None,
    supply_v: Optional[float] = None,
    series: str = "E12",
) -> Dict[str, Any]:
    """Size the one resistor in an LED + switch + cell loop, and say why.

    The arithmetic is Ohm's law across the part of the supply the LED does not
    use::

        headroom = supply_voltage - LED_forward_voltage
        R        = headroom / target_current

    then rounded **up** to the next value in the E12 series, because a resistor
    that is slightly too big dims the LED and a resistor that is slightly too
    small cooks it.

    ``current_ma`` defaults to the LED's nominal 20 mA -- the number every
    tutorial uses and the one that makes the answer recognisable.  The plan then
    also reports ``resistor_gentle_ohms``: the same maths at the *cell's* own
    recommended current, which for a coin cell is a great deal less than 20 mA
    and is what you should actually build if you want the thing to still be lit
    tomorrow.

    Three outcomes, and the plan names which one you got:

    ``"no resistor needed"``
        The headroom is zero or negative -- a white or blue LED on a 3 V coin
        cell.  There is nothing for a resistor to drop.  The cell's own internal
        resistance limits the current.
    ``"resistor optional"``
        Small headroom on a supply that limits itself.  It will work without
        one; the plan says what you trade.
    ``"resistor required"``
        Everything else.  Fit it.
    """
    led_entry = _components.require_category(led, "light")
    colour = _components.led_forward_voltage(
        color if color is not None else led_entry["electrical"]["default_color"]
    )
    supply = _supply(cell, cells, supply_v)

    switch_entry = None
    if switch is not None:
        switch_entry = _components.require_category(switch, "switch")

    v_f = float(colour["vf"])
    v_supply = float(supply["voltage_v"])
    headroom = v_supply - v_f

    nominal_ma = float(led_entry["electrical"]["nominal_current_ma"])
    max_ma = float(led_entry["electrical"]["max_current_ma"])
    target_ma = nominal_ma if current_ma is None else _positive(current_ma, "current_ma")
    if target_ma > max_ma + 1e-9:
        raise CircuitError(
            f"{target_ma:g} mA is over the {led_entry['name']}'s {max_ma:g} mA "
            f"maximum. Ask for {max_ma:g} mA or less."
        )

    notes: List[str] = []
    warnings: List[str] = []

    if headroom <= 0.02:
        verdict = "no resistor needed"
        resistor = 0.0
        exact = 0.0
        # Nothing but the supply's own internal resistance limits it, and the
        # voltage doing the pushing is the FRESH voltage, not the nominal one:
        # a nominally-3 V cell reads 3.2 V out of the packet, and that 0.2 V
        # across 30 ohm is the entire circuit.
        limiting = supply["internal_resistance_ohm"]
        fresh_headroom = float(supply.get("fresh_voltage_v") or v_supply) - v_f
        if limiting > 0.0 and fresh_headroom > 0.0:
            actual_ma = round(1000.0 * fresh_headroom / limiting, 3)
        else:
            actual_ma = None
        reason = (
            f"a {colour['color']} LED drops {v_f:g} V and the supply is only "
            f"{v_supply:g} V, so there is {max(headroom, 0.0):.2f} V left for a "
            "resistor to drop -- which is to say, none. Wire the LED straight to "
            "the cell through the switch."
        )
        if supply["current_limited"]:
            notes.append(
                f"The {supply['name']}'s own internal resistance (about "
                f"{supply['internal_resistance_ohm']:g} ohm) is the current limit "
                "here. That is a real limit, not a hope: it is why an LED taped to "
                "a coin cell glows for a day instead of exploding."
                + (
                    f" A fresh cell reads {supply['fresh_voltage_v']:g} V, so it "
                    f"starts at roughly {actual_ma:.0f} mA and falls away as the "
                    "cell's voltage sags towards the LED's forward voltage -- the "
                    "light dims steadily rather than going out."
                    if actual_ma
                    else ""
                )
            )
        else:
            warnings.append(
                "This supply is NOT current-limited, and the LED has no headroom to "
                "share with a resistor. Nothing is protecting the LED except the "
                "forward voltage matching the supply, and a fresh cell reads higher "
                "than its nominal voltage. Use a lower-voltage supply, a different "
                "colour, or accept that the LED is running on luck."
            )
        forward_current_ma = actual_ma
    else:
        exact = headroom / (target_ma / 1000.0)
        resistor = preferred_resistor(exact, series)
        forward_current_ma = 1000.0 * headroom / resistor if resistor else target_ma
        optional = (
            supply["current_limited"] and headroom <= COIN_CELL_OPTIONAL_HEADROOM_V
        )
        verdict = "resistor optional" if optional else "resistor required"
        reason = (
            f"({v_supply:g} V - {v_f:g} V) / {target_ma:g} mA = {exact:.1f} ohm, "
            f"rounded UP to the next {series.upper()} value: {resistor:g} ohm. "
            "Rounding up is the safe direction -- too big only dims the LED."
        )
        if optional:
            notes.append(
                f"You can leave the resistor out. {headroom:.1f} V of headroom "
                f"against the {supply['name']}'s own "
                f"{supply['internal_resistance_ohm']:g} ohm means the cell limits "
                f"the current to roughly "
                f"{1000.0 * headroom / max(supply['internal_resistance_ohm'], 1e-6):.0f} "
                "mA all by itself. The trade: without the resistor the LED is "
                "brighter at first and the cell is flat in a fraction of the time, "
                "and the brightness visibly falls as the cell ages. With it, the "
                "brightness is steady and the cell lasts. Fit it if the thing is "
                "meant to be used; skip it if you are testing on the bench."
            )

    amps = (forward_current_ma or 0.0) / 1000.0
    power_w = amps * amps * resistor
    rating_w = power_rating_for(power_w)

    gentle_ma = supply["recommended_current_ma"]
    gentle_ohms = None
    gentle_runtime = None
    if gentle_ma and headroom > 0.02:
        gentle_ohms = preferred_resistor(headroom / (gentle_ma / 1000.0), series)
        gentle_runtime = _runtime_hours(supply["capacity_mah"], gentle_ma)

    draw_ma = forward_current_ma if forward_current_ma else target_ma
    runtime = _runtime_hours(supply["capacity_mah"], draw_ma)

    if supply["recommended_current_ma"] and draw_ma > supply["recommended_current_ma"] + 1e-9:
        notes.append(
            f"{draw_ma:.1f} mA is more than the {supply['name']} likes to give "
            f"({supply['recommended_current_ma']:g} mA is its comfortable "
            "continuous draw). It will work and it will be bright; it will also be "
            "flat sooner than the capacity suggests, because a coin cell's voltage "
            "sags under a load like this."
            + (
                f" A {gentle_ohms:g} ohm resistor instead brings it to "
                f"{gentle_ma:g} mA and roughly {gentle_runtime:g} hours."
                if gentle_ohms and gentle_runtime
                else ""
            )
        )

    if switch_entry is not None:
        max_switch_ma = float(switch_entry["electrical"]["max_current_ma"])
        if draw_ma > max_switch_ma:
            warnings.append(
                f"{draw_ma:.1f} mA is over the {switch_entry['name']}'s "
                f"{max_switch_ma:g} mA rating. Pick a bigger switch or a smaller "
                "current."
            )

    order = ["cell +"]
    if switch_entry is not None:
        order.append(f"{switch_entry['name']}")
    if resistor:
        order.append(f"resistor {resistor:g} ohm")
    order.extend([f"{led_entry['name']} + (long leg)",
                  f"{led_entry['name']} - (short leg, flat side)", "cell -"])

    return {
        "verdict": verdict,
        "reason": reason,
        "supply": supply,
        "led": {
            "name": led_entry["name"],
            "color": colour["color"],
            "forward_voltage_v": v_f,
            "forward_voltage_band_v": list(colour["band"]),
            "forward_voltage_note": colour["note"],
            "nominal_current_ma": nominal_ma,
            "max_current_ma": max_ma,
            "polarity": led_entry["polarity"],
        },
        "switch": (
            {
                "name": switch_entry["name"],
                "latching": bool(switch_entry["actuation"]["latching"]),
                "max_current_ma": float(switch_entry["electrical"]["max_current_ma"]),
                "leads_note": switch_entry["leads"]["note"],
            }
            if switch_entry
            else None
        ),
        "headroom_v": round(headroom, 4),
        "target_current_ma": round(target_ma, 4),
        "resistor_exact_ohms": round(exact, 2),
        "resistor_ohms": resistor,
        "resistor_series": series.upper(),
        "resistor_power_w": round(power_w, 5),
        "resistor_rating_w": rating_w,
        "resistor_gentle_ohms": gentle_ohms,
        "resistor_gentle_current_ma": gentle_ma,
        "resistor_gentle_runtime_h": gentle_runtime,
        "forward_current_ma": (
            round(forward_current_ma, 3) if forward_current_ma is not None else None
        ),
        "runtime_hours": runtime,
        "runtime_note": (
            f"{supply['capacity_mah']:g} mAh derated to "
            f"{_CAPACITY_DERATE:g} of nameplate, divided by {draw_ma:.1f} mA. Real "
            "life is shorter still: the LED dims long before the cell is empty."
            if runtime
            else "no capacity on file for this supply"
        ),
        "series_order": order,
        "notes": notes,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------
# The workflow, in words a beginner can follow
# --------------------------------------------------------------------------


def _sentence(text: Any) -> str:
    """One clause from the component table, ended with exactly one full stop."""
    body = str(text).strip()
    while body.endswith((".", " ")):
        body = body[:-1].rstrip()
    return body + "."


def wiring_steps(plan: Mapping[str, Any]) -> List[str]:
    """Soldering and assembly steps for the circuit :func:`circuit_plan` sized.

    Written for somebody who has never soldered.  The order is the order the
    mistakes happen in: polarity first, because it is the one that makes nothing
    work at all; the test step before the glue step, because glue is the point
    of no return.
    """
    led = plan["led"]
    supply = plan["supply"]
    switch = plan.get("switch")
    resistor = float(plan.get("resistor_ohms") or 0.0)

    steps: List[str] = []

    steps.append(
        "Lay the parts out on the bench in the order they will be wired and leave "
        "them there: "
        + " then ".join(str(item) for item in plan["series_order"])
        + ". Everything below is that one loop. There are no branches, no second "
        "wires, and nothing is connected to anything twice."
    )

    steps.append(
        "Find the LED's positive leg NOW, before you trim anything. "
        + _sentence(led["polarity"])
        + " If both legs are already short, look at the plastic rim: it has a "
        "flat filed on one side, and the leg beside the flat is the negative one. "
        "Backwards is not dangerous -- it simply does not light -- but it is the "
        "single most common reason a first circuit does nothing."
    )

    steps.append(
        "Find the supply's polarity: " + _sentence(supply["polarity"]) + " Mark the "
        "positive side with a dot of paint or a wrap of red tape if there is any "
        "chance of forgetting once it is inside the model."
    )

    if switch:
        steps.append(
            "Work out which two switch terminals are the ones that open and close. "
            + _sentence(switch["leads_note"])
            + " Do this with a multimeter on its continuity setting and the switch "
            "in your hand -- it takes a minute and it saves unsoldering a switch "
            "out of a finished model, which takes an hour."
        )

    if resistor:
        steps.append(
            f"Solder the {resistor:g} ohm resistor to the LED's LONG (+) leg. A "
            "resistor has no polarity, so either end will do. Snip the joined legs "
            f"back to about 5 mm first. A {plan['resistor_rating_w']:g} W resistor "
            f"is plenty here -- it has to get rid of {plan['resistor_power_w'] * 1000.0:.1f} "
            "mW, which it will not even notice."
        )
        positive_tail = "the free end of the resistor"
    else:
        steps.append(
            "There is no resistor in this circuit: "
            + _sentence(plan["reason"])
            + " If that makes you uneasy, a 100 ohm resistor in the same place "
            "costs you a little brightness and buys you a lot of margin; it is "
            "never the wrong thing to add."
        )
        positive_tail = "the LED's LONG (+) leg"

    if switch:
        steps.append(
            f"Solder a short wire from {positive_tail} to one of the two switch "
            "terminals you identified. Keep the wire long enough to route but "
            "short enough not to loop around inside the model -- 40 to 60 mm is "
            "usually right."
        )
        steps.append(
            "Solder a second wire from the switch's other terminal to the supply's "
            "POSITIVE side. The switch simply interrupts the positive side of the "
            "loop; it does not care which way round it goes."
        )
    else:
        steps.append(
            f"Solder a wire from {positive_tail} to the supply's POSITIVE side."
        )

    steps.append(
        "Solder the last wire from the LED's SHORT (-) leg to the supply's "
        "NEGATIVE side. The loop is now closed and the circuit is finished."
    )

    steps.append(
        "Insulate every joint before anything goes inside the model. Heat-shrink "
        "sleeving slid on BEFORE you solder is the tidy way; a tight wrap of "
        "electrical tape is the way that works. Pay particular attention to the "
        "LED's two legs -- they are 2.5 mm apart, they are bare metal, and once "
        "the model is closed a leg that has bent across to touch its neighbour "
        "looks exactly like a flat battery."
    )

    steps.append(
        "TEST IT NOW, on the bench, before a single drop of glue. Fit the cell, "
        "press the switch, and watch the LED. "
        + (
            "Press again: it should go off and stay off. A latching switch that "
            "only lights while held is wired to the wrong pair of terminals."
            if switch and switch.get("latching")
            else "It should light while the switch is operated."
        )
        + " If nothing happens, in this order: turn the LED round, check the cell "
        "is the right way up, check the switch pair with the meter. It is one of "
        "those three about nineteen times in twenty."
    )

    steps.append(
        "Now fit it into the printed parts, and glue only what has to be glued. "
        "A dab of hot glue on the battery holder and on the wire where it leaves a "
        "channel is enough. Do NOT glue the LED, the switch or the cell holder in "
        "permanently on the first build -- every one of them is a part you will "
        "want to change, and the LED in particular is the part you will want to "
        "swap for a different colour once you see it lit through the print."
    )

    steps.append(
        "Route the wires into the channels, not across the mechanism. A wire lying "
        "under a plunger or across a switch button turns a 250 gram press into a "
        "press that never quite works, and it is invisible once the model is shut."
    )

    if plan.get("runtime_hours"):
        steps.append(
            f"Expect roughly {plan['runtime_hours']:g} hours of light from one "
            f"{supply['name']}. " + _sentence(plan["runtime_note"]) + " Build the battery "
            "door so you can actually get at it: this is the part of the model you "
            "will open most often and the part most likely to be glued shut in a "
            "moment of enthusiasm."
        )

    for warning in plan.get("warnings") or ():
        steps.append("WARNING: " + str(warning))

    return steps


def bill_of_materials(plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The shopping list implied by a circuit plan, one row per line item."""
    rows: List[Dict[str, Any]] = []
    led_name = plan["led"]["name"]
    led_entry = _components.component(led_name)
    rows.append({
        "item": f"{led_name} ({plan['led']['color']})",
        "quantity": 1,
        "source": "purchased",
        "note": led_entry["purchase_note"],
    })
    if plan.get("switch"):
        switch_entry = _components.component(plan["switch"]["name"])
        rows.append({
            "item": switch_entry["name"],
            "quantity": 1,
            "source": "purchased",
            "note": switch_entry["purchase_note"],
        })
    if plan["supply"].get("name"):
        cell_entry = _components.component(plan["supply"]["name"])
        rows.append({
            "item": cell_entry["name"],
            "quantity": int(plan["supply"]["cells"]),
            "source": "purchased",
            "note": cell_entry["purchase_note"],
        })
    if plan.get("resistor_ohms"):
        rows.append({
            "item": f"resistor {plan['resistor_ohms']:g} ohm "
                    f"{plan['resistor_rating_w']:g} W",
            "quantity": 1,
            "source": "purchased",
            "note": (
                "Sold in assortment books of 600 for the price of a coffee. Buy the "
                "book: you will want a different value the first time you see the "
                "LED lit through the actual print."
            ),
        })
    rows.append({
        "item": "hook-up wire, 26 AWG stranded",
        "quantity": 1,
        "source": "purchased",
        "note": "About 300 mm. Stranded, not solid -- solid wire work-hardens and "
                "snaps off at the solder joint the third time you open the model.",
    })
    rows.append({
        "item": "heat-shrink sleeving or electrical tape",
        "quantity": 1,
        "source": "purchased",
        "note": "For every joint. Not optional in a sealed model.",
    })
    return rows


# --------------------------------------------------------------------------
# A diagram, drawn as string formatting
# --------------------------------------------------------------------------

_SVG_STYLE = (
    "<style>"
    ".w{fill:none;stroke:#222;stroke-width:2}"
    ".b{fill:#fff;stroke:#222;stroke-width:2}"
    ".t{font:12px sans-serif;fill:#222;text-anchor:middle}"
    ".s{font:10px sans-serif;fill:#666;text-anchor:middle}"
    "</style>"
)


def _svg_block(x: float, y: float, w: float, h: float, label: str,
               sub: str = "") -> str:
    parts = [
        f'<rect class="b" x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="4"/>',
        f'<text class="t" x="{x + w / 2:g}" y="{y + h / 2 + 4:g}">{label}</text>',
    ]
    if sub:
        parts.append(f'<text class="s" x="{x + w / 2:g}" y="{y + h + 14:g}">{sub}</text>')
    return "".join(parts)


def diagram_svg(plan: Mapping[str, Any]) -> str:
    """The circuit as one rectangular loop of boxes and lines, as an SVG string.

    Deliberately crude: four sides of a rectangle, a box on each side that has a
    part in it, and a ``+`` marking which way round the loop goes.  It is not a
    schematic and it does not want to be -- it is the picture that stops somebody
    wiring the switch across the LED instead of in series with it.
    """
    led = plan["led"]
    supply = plan["supply"]
    switch = plan.get("switch")
    resistor = float(plan.get("resistor_ohms") or 0.0)

    width, height = 420, 220
    left, right, top, bottom = 40, 380, 40, 180

    out: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" '
        f'aria-label="LED circuit loop">',
        _SVG_STYLE,
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#fff"/>',
        # the loop
        f'<path class="w" d="M{left} {top} H{right} V{bottom} H{left} Z"/>',
    ]

    # Supply on the left edge.
    out.append(_svg_block(left - 34, (top + bottom) / 2 - 26, 68, 52,
                          "CELL", supply.get("name") or "supply"))
    out.append(f'<text class="t" x="{left + 22:g}" y="{top + 60:g}">+</text>')
    out.append(f'<text class="t" x="{left + 22:g}" y="{bottom - 46:g}">-</text>')

    # Switch on the top edge.
    top_boxes: List[Tuple[str, str]] = []
    if switch:
        top_boxes.append(("SWITCH", switch["name"]))
    if resistor:
        top_boxes.append((f"{resistor:g}R", f"{plan['resistor_rating_w']:g} W"))
    span = right - left
    for index, (label, sub) in enumerate(top_boxes):
        centre = left + span * (index + 1) / (len(top_boxes) + 1)
        out.append(_svg_block(centre - 46, top - 18, 92, 36, label, sub))

    # LED on the right edge.
    out.append(_svg_block(right - 34, (top + bottom) / 2 - 26, 68, 52,
                          "LED", f"{led['color']} {led['forward_voltage_v']:g} V"))
    out.append(f'<text class="t" x="{right - 22:g}" y="{top + 60:g}">+ long leg</text>')
    out.append(f'<text class="t" x="{right - 26:g}" y="{bottom - 46:g}">- flat side</text>')

    out.append(
        f'<text class="s" x="{width / 2:g}" y="{bottom + 28:g}">'
        f'one loop, in series: ' + " &#8594; ".join(
            str(item) for item in plan["series_order"]
        ) + '</text>'
    )
    out.append("</svg>")
    return "".join(out)


__all__ = [
    "COIN_CELL_OPTIONAL_HEADROOM_V",
    "CircuitError",
    "E12",
    "E24",
    "SERIES",
    "bill_of_materials",
    "circuit_plan",
    "diagram_svg",
    "power_rating_for",
    "preferred_resistor",
    "wiring_steps",
]
