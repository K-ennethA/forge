from build123d import *  # noqa: F403

PARAMS = {
    "radius": {"value": 73.5, "unit": "mm", "min": 10.0, "max": 200.0, "description": "Bore radius"},
    "height": {"value": 40.0, "unit": "mm", "min": 5.0, "max": 100.0, "description": "Cutter height, overshoots both ends"},
}


def build(p):
    return Cylinder(radius=p["radius"], height=p["height"])
