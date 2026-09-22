"""A plain box cutter for surgical removal of stray geometry."""

from build123d import *  # noqa: F403

PARAMS = {
    "width": {"value": 20.0, "unit": "mm", "min": 1.0, "max": 100.0, "description": "X size"},
    "depth": {"value": 20.0, "unit": "mm", "min": 1.0, "max": 100.0, "description": "Y size"},
    "height": {"value": 20.0, "unit": "mm", "min": 1.0, "max": 100.0, "description": "Z size"},
}


def build(p):
    return Box(p["width"], p["depth"], p["height"])
