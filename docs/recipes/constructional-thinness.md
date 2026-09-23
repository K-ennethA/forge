# Taper-to-zero thinness is constructional — rebuild, never patch

## WHEN

`min_wall` fails where two faces of a shell CONVERGE — a tapered tip, a
root/boundary ring where a loft closes, any wedge — and a fix attempt moves
the thin probe to a nearby spot instead of clearing it.

## THE RECIPE

1. Recognize the signature: after a local fix, the thinnest probe RELOCATES
   within the same band at a similar value. That band's thinness is a
   property of the construction (walls meeting at an edge), not a defect on
   top of it. Local vertex pushes, trims and cap fills cannot create
   thickness where the construction pinches to zero — a horizontal cut
   through a wedge exposes a knife edge wherever you cut.
2. Fix it in the GENERATOR: rebuild the shell with the minimum separation
   enforced at that boundary in the construction itself — a blunt ring or
   blunted tip with >= 1.2 mm between inner and outer walls, before the
   walls join. A tip fixed this way stays fixed.
3. Verify with the gate's own probes over the WHOLE band after rebuild, not
   just the last reported point — the probe you saw is one sample of a
   continuous thin region.

## WHY (measured)

Benchmark ear-sculpt, 2026-09-23, six attempts on one root band: agent local
thickening twice (thinnest 0.029 -> 0.289 -> 0.267 mm, probe moved along the
band), orchestrator base trim (worse: coplanar cap faces, 0.130 mm), normal-
raycast displacement (zero verts moved — wedge rays exit the open side, the
method is blind exactly where the defect lives), horizontal band push
(worse: 0.001 mm), and a generator rebuild with a blunt root that still left
probes below the ring (0.026 mm at z 60.4). The tip, fixed IN CONSTRUCTION
in one attempt, stayed clean through every subsequent edit.

## REJECTED

- Local vertex displacement near a wedge — raycasts along normals miss the
  pinch (0 verts found at a 0.13 mm wall).
- Cutting off the thin region and capping — the cap meets the wall at the
  same acute angle; nested-loop caps measure ~0 between coplanar faces.
- Chasing the reported probe point — it is one sample of a band.

## SOURCE

benchmark/results/2026-09-23/ (ear-v2 series), scratchpad measurement logs
in the session record; the tip fix that worked: the ear rebuild conversation,
"blunted tip, silhouette 0.947, tip clean thereafter". Siblings:
[sculpt-for-print-thickening.md](sculpt-for-print-thickening.md) (for holes
and booleans on otherwise-thick shells), [organic-path-choice.md](organic-path-choice.md).
