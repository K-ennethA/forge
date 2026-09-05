"""Reference PartForge script for the appendage-slot library.

One script, two parts.  ``make_appendage`` switches between

* the **base**: a plate with a keyed socket bored into its top face, and
* the **appendage**: an ear with the matching keyed peg on its underside.

Both come from the same ``forge_lib.peg_spec(...)``, which is the whole point --
change ``peg_diameter`` and the socket moves with it, so a base printed today
still takes an ear designed tomorrow.

``forge_lib`` is available to every PartForge script the same way ``build123d``
is; the ``import`` below is for the reader (and for editors), not a requirement.
"""

from build123d import *  # noqa: F403 - the build123d house style

import forge_lib  # noqa: F401 - provided by the geometry service

PARAMS = {
    "peg_diameter": {
        "value": 6.0,
        "unit": "mm",
        "min": 3.0,
        "max": 20.0,
        "step": 0.5,
        "description": "Peg diameter; the socket follows it",
    },
    "peg_length": {
        "value": 8.0,
        "unit": "mm",
        "min": 3.0,
        "max": 40.0,
        "step": 0.5,
        "description": "How deep the peg goes in",
    },
    "fit": {
        "value": 0.2,
        "unit": "mm",
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "description": "Socket clearance: 0.2 mm slide fit, 0.1 mm press fit",
    },
    "base_size": {
        "value": 30.0,
        "unit": "mm",
        "min": 10.0,
        "max": 200.0,
        "step": 1.0,
        "description": "Side of the square base plate",
    },
    "base_height": {
        "value": 12.0,
        "unit": "mm",
        "min": 4.0,
        "max": 100.0,
        "step": 1.0,
        "description": "Base plate thickness; must clear the peg length",
    },
    "make_appendage": {
        "value": False,
        "unit": "bool",
        "description": "False: the base with its socket. True: the ear that plugs in",
    },
}


def build(p):
    spec = forge_lib.peg_spec(d=p["peg_diameter"], l=p["peg_length"])

    if p["make_appendage"]:
        ear = Pos(0, 0, p["peg_length"] + 6.0) * Box(10.0, 6.0, 12.0)  # noqa: F405
        return ear + forge_lib.peg(spec)

    side = p["base_size"]
    height = p["base_height"]
    if height <= p["peg_length"] + 1.0:
        raise ValueError(
            f"base_height ({height} mm) must clear the peg ({p['peg_length']} mm) "
            "with at least 1 mm of floor under the socket"
        )

    base = Pos(0, 0, height / 2.0) * Box(side, side, height)  # noqa: F405
    # The socket is built pointing +Z from Z=0, so flip it and put its mouth on
    # the top face: it then bores downwards into the plate.
    socket = (
        Pos(0, 0, height)  # noqa: F405
        * Rot(180, 0, 0)  # noqa: F405
        * forge_lib.socket_for(spec, p["fit"])
    )
    return base - socket
