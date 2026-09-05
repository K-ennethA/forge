# bowl-holder

The Phase 1 exit test for PartForge: the bowl-holder **base ring** and the
matching **collar band**, both real Build123d parts driven entirely by their
`PARAMS` blocks.

## What it is

A footed collar for a 6 in (152.4 mm) stainless mixing bowl.

The bowl is lowered through the top bore and its rim lands on an internal seat
lip; below the seat the bore opens back up so the bowl's body hangs free. The
lower outside carries a band of scalloped flutes, the base is an arcaded
plinth whose piers are the feet, and two lugs on the X axis carry blind
sockets for slot-in ears (a separate appendage part, not in this folder).

The collar band is a second printed piece: the same flute family on a plain
sleeve that slides down over the ring's outer wall on a 0.2 mm slide fit.

| File | What it is |
|---|---|
| `part_base_ring.py` | The main holder ring. 12 parameters. |
| `part_collar_band.py` | The separate decorative sleeve. 8 parameters. |
| `spec.json` | The editable contract: parameters, features, appendage slots, print settings. |

Both scripts follow the PARAMS contract in `docs/architecture.md`: a top-level
`PARAMS` dict where every entry has a `value` and a `unit`, and a top-level
`build(p)` that works in millimetres and returns a Build123d `Part`. The part
sits on Z = 0 in its print orientation.

## Key numbers at the defaults

| | Base ring | Collar band |
|---|---|---|
| Bore | 154.0 mm (152.4 + 2 x 0.8 clearance) | 164.4 mm (164.0 + 2 x 0.2 slide fit) |
| Bounding box | 180.0 x 169.0 x 62.0 mm | 172.4 x 172.4 x 20.0 mm |
| Seat lip | inner edge at r 70.5 mm, top face at z 52.1 mm | -- |
| Flute band | z 14.0 to 41.1 mm, 24 flutes, 1.6 mm deep | z 3.0 to 17.0 mm, 24 flutes, 1.6 mm deep |
| Mesh | 2684 verts / 5368 tris, watertight | 990 verts / 1980 tris, watertight |

Both fit the Centauri Carbon bed (256 x 256 x 256) at *every* declared
parameter value; the widest case in range measures 246 mm.

## Regenerating and exporting

The geometry service does the work. With the service running on
`127.0.0.1:8765` (see `service/README.md`):

```bash
# resolved parameter schema, no geometry
curl -X POST http://127.0.0.1:8765/parse_params \
  -H "Content-Type: application/json" \
  -d "{\"script\": $(python -c "import json,sys;print(json.dumps(open(sys.argv[1],encoding='utf-8').read()))" part_base_ring.py)}"

# mesh + stats
POST /generate  {"script": "<source>", "overrides": {"feet_count": 6}}

# STL / STEP / 3MF to an absolute path
POST /export    {"script": "<source>", "overrides": {}, "format": "stl",
                 "path": "C:\\...\\projects\\bowl-holder\\exports\\base_ring.stl"}
```

In Blender, the Forge panel reads the same `PARAMS` block and gives you a
slider per parameter; every change re-runs `build(p)` and reloads the mesh in
place.

Straight from Python, without the HTTP layer:

```python
import sys; sys.path.insert(0, r"C:\...\forge")
from service.runner import run_generate, run_export

source = open(r"...\part_base_ring.py", encoding="utf-8").read()
print(run_generate(source, {})["stats"])
run_export(source, "stl", r"...\exports\base_ring.stl", {})
```

Run it with `service/.venv/Scripts/python.exe` — that is the interpreter with
Build123d 0.11.1 installed.

## Turning the knobs

* **A different bowl.** Set `bowl_diameter` (declared in inches, 3.0–7.0) and
  leave `fit_clearance` at 0.8 mm. Then set the collar's
  `ring_outer_diameter` to `bowl_diameter_mm + 2 * (fit_clearance +
  wall_thickness)` so it still slides on.
* **Texture.** `flute_count` 6–60 and `flute_depth` 0.3–4.0 mm on both parts.
  Low counts read as wide facets, high counts as fine reeding. Depth is
  silently capped at 40 % of the wall and at whatever the circumferential
  pitch can hold, so the band never breaks into the bore and the flutes never
  merge into knife edges.
* **Feet.** `feet_count` 3–8 arches; `foot_height` sets how tall the arcade is.
* **Ears.** `socket_diameter` / `socket_depth` size the two blind sockets. The
  matching peg is 5.8 mm x 9.0 mm (`spec.json` -> `appendage_slots`): the
  socket is the peg plus the printer's 0.2 mm slide fit, and 1 mm deeper than
  the peg is long so the ear seats on its shoulder.

Parameters that would otherwise fight each other are clamped inside `build()`
rather than rejected, so every combination of the declared min/max values
produces a valid, watertight solid. At the extreme short-and-stubby end the
flute band can shrink below 3 mm, in which case the band is left plain rather
than cut with unprintable slivers.

## Printing notes

Print as modelled, feet on the bed, no support intended:

* the seat lip's underside is a taper, not a flat ledge — at the defaults it
  rises 8 mm over 6.5 mm, so 51° from horizontal, i.e. a 39° overhang against
  the profile's 50° limit;
* the arcade openings have round tops;
* the ear lugs have domed (spherical) undersides where they leave the wall.

The one place to watch is the very bottom of each ear lug: a dome bottoms out
at a horizontal tangent, so a stringy filament may want a touch of support
there. Raising `socket_depth` (which raises the lug) or dropping
`socket_diameter` (which shrinks the dome) both reduce it.
