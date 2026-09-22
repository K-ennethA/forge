"""A fillet skirt for a peg-to-root junction: a truncated cone, wide at the
buried (ear/tail-material) end, tapering down to the peg's own radius at
the transition point where the bare peg shaft begins. Pre-shifted so its
NARROW end sits at local Z=0 -- attach with the exact same Pos/Rot used for
the peg itself, and the two blend with no seam."""

from build123d import *  # noqa: F403

PARAMS = {
    "peg_r": {"value": 3.0, "unit": "mm", "min": 1.0, "max": 10.0, "description": "Peg radius (skirt's narrow end)"},
    "flare": {"value": 3.0, "unit": "mm", "min": 0.5, "max": 10.0, "description": "How much wider the buried end is than the peg"},
    "height": {"value": 3.0, "unit": "mm", "min": 1.0, "max": 10.0, "description": "Skirt height"},
}


def build(p):
    cone = Cone(bottom_radius=p["peg_r"] + p["flare"], top_radius=p["peg_r"], height=p["height"])
    return Pos(0, 0, -p["height"]) * cone
