"""A standalone keyed peg, for booleaning onto a sculpted appendage root."""

import forge_lib  # noqa: F401

PARAMS = {
    "diameter": {"value": 6.0, "unit": "mm", "min": 2.0, "max": 12.0, "description": "Peg diameter"},
    "length": {"value": 11.0, "unit": "mm", "min": 4.0, "max": 20.0, "description": "Peg length"},
}


def build(p):
    return forge_lib.peg(d=p["diameter"], l=p["length"])
