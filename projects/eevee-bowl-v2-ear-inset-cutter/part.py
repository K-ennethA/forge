"""A small rounded cutter for the ear's inner-ear inset.

A stadium-shaped (box + full fillet) lens, thin in one axis, meant to be
positioned against the ear's front face and subtracted so the inner ear
reads as a real 1-1.5 mm recess -- geometry, not paint.
"""

from build123d import *  # noqa: F403

PARAMS = {
    "width": {"value": 6.0, "unit": "mm", "min": 2.0, "max": 20.0,
               "description": "Across the inset"},
    "length": {"value": 45.0, "unit": "mm", "min": 5.0, "max": 80.0,
                "description": "Along the inset"},
    "depth": {"value": 6.0, "unit": "mm", "min": 1.0, "max": 20.0,
               "description": "Through-thickness of the cutter (deep enough to punch past the ear's own thickness)"},
}


def build(p):
    box = Box(p["width"], p["depth"], p["length"])
    try:
        box = fillet(box.edges(), radius=min(p["width"], p["depth"]) * 0.48)
    except Exception:
        pass
    return box
