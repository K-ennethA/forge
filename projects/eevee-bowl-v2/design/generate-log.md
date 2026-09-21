# Generate stage — per-piece numbers

All four pieces built from `part.py` and landed in Blender, collection
`eevee-bowl-v2`, all watertight.

| Piece | Object | Verts | Faces | Bounding box (mm) |
|---|---|---|---|---|
| Base | eevee-bowl-v2 | 7416 | 14852 | 173.48 x 170.27 x 76.8 |
| Ear (L) | eevee-bowl-v2-ear-l | 1531 | 3058 | 30.06 x 81.06 x 9 |
| Ear (R) | eevee-bowl-v2-ear-r | 1531 | 3058 | 30.06 x 81.06 x 9 |
| Tail | eevee-bowl-v2-tail | 1331 | 2658 | 29.3 x 88.42 x 9 |
| Fur collar | eevee-bowl-v2-collar | 3694 | 7388 | 197.5 x 198.01 x 36.44 |

The collar's own bounding box (197.5 mm across) is wider than the base
(173.48 mm) -- expected, not a defect: the leaves droop outward at 44
degrees from the neck they slide onto, same as the reference photo's flare.
Every bbox is comfortably inside the 256 mm bed with margin to spare, so no
piece should need segmentation.
