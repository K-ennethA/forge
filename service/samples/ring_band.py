"""Reference PartForge script: a parametric ring band.

This is the shape every Claude-generated script should have.

* A top-level ``PARAMS`` dict.  Each entry has a ``value`` and a ``unit``, plus
  ``min``/``max``/``step``/``description`` so the Blender panel gets a usable
  slider instead of a bare number field.
* A top-level ``build(p)`` that takes the resolved parameters and returns a
  Build123d ``Part``/``Solid``/``Compound``.

Contract reminders:

* ``build(p)`` works in **millimetres**.  A parameter declared ``"unit": "in"``
  arrives already multiplied by 25.4; a ``"deg"`` parameter arrives in degrees;
  ``"count"`` arrives as ``int``; ``"bool"`` as ``bool``.
* ``build(p)`` must be a pure function of ``p`` -- no globals mutated, no files
  read.  It is called again on every slider change.
* Raise a plain exception with a human-readable message when the parameters
  cannot make a valid solid.  The service turns it into an HTTP 400 and the
  panel shows the message.
* The part is modelled sitting on Z=0 so it lands on the print bed the way it
  will be sliced.
"""

from build123d import *  # noqa: F403 - the build123d house style

PARAMS = {
    "outer_diameter": {
        "value": 20.0,
        "unit": "mm",
        "min": 4.0,
        "max": 300.0,
        "step": 0.5,
        "description": "Outer diameter of the band",
    },
    "height": {
        "value": 8.0,
        "unit": "mm",
        "min": 0.5,
        "max": 200.0,
        "step": 0.5,
        "description": "Height of the band along Z",
    },
    "wall_thickness": {
        "value": 2.0,
        "unit": "mm",
        "min": 0.4,
        "max": 50.0,
        "step": 0.1,
        "description": "Radial wall thickness",
    },
    "chamfer_edges": {
        "value": True,
        "unit": "bool",
        "description": "Break the top and bottom rims with a chamfer",
    },
    "chamfer_size": {
        "value": 0.5,
        "unit": "mm",
        "min": 0.05,
        "max": 5.0,
        "step": 0.05,
        "description": "Chamfer size, clamped to fit the wall and height",
    },
}

#: Below this the chamfer is smaller than any nozzle can print, so skip it.
_MIN_USEFUL_CHAMFER_MM = 0.01


def build(p):
    """Return the ring band as a Build123d ``Part``."""
    outer_radius = p["outer_diameter"] / 2.0
    wall = p["wall_thickness"]
    height = p["height"]
    inner_radius = outer_radius - wall

    if inner_radius <= 0.0:
        raise ValueError(
            f"wall_thickness ({wall} mm) must be less than the outer radius "
            f"({outer_radius} mm); the band would have no hole"
        )

    # A chamfer cannot eat more than half the wall (the inner and outer
    # chamfers would meet) or half the height (top and bottom would meet).
    max_chamfer = min(wall, height) / 2.0 * 0.98
    chamfer_size = min(float(p["chamfer_size"]), max_chamfer)

    with BuildPart() as band:  # noqa: F405
        with BuildSketch(Plane.XY):  # noqa: F405
            Circle(radius=outer_radius)  # noqa: F405
            Circle(radius=inner_radius, mode=Mode.SUBTRACT)  # noqa: F405
        extrude(amount=height)  # noqa: F405

        if p["chamfer_edges"] and chamfer_size >= _MIN_USEFUL_CHAMFER_MM:
            # The four rim circles: outer top/bottom and inner top/bottom.
            rims = band.edges().filter_by(GeomType.CIRCLE)  # noqa: F405
            chamfer(rims, length=chamfer_size)  # noqa: F405

    return band.part
