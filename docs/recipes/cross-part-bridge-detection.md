# Cross-part bridge detection

## WHEN

Verifying a retopo'd, tagged mesh before skinning (or any time a mesh with
`tag_*` groups needs checking for silent welds between body parts that
should stay separate) — a weld that will read as clean because every vertex
still follows its correct bone, but that tears into a visible membrane the
moment the two parts move apart.

## THE RECIPE

1. Run the tag-adjacency check at the `verify_mesh` stage, **before**
   skinning — it needs only the mesh and its tags, no rig and no pose.
2. **The primary signal**: an edge whose two endpoints' dominant `tag_*`
   groups name two parts that are **not** anatomically adjacent is a bridge,
   full stop.
3. **Within a legally adjacent pair**, a contact patch is only suspect: flag
   it when its centroid sits farther than `BRIDGE_OUTLIER_FACTOR` (6.0)
   median edge lengths from that pair's largest patch. Report this signal —
   never let it fail the gate on its own; it is a weaker, spatial-outlier
   heuristic, not the tag rule.
4. Skip the scan above `BRIDGE_VERTEX_LIMIT` vertices (it's a per-vertex
   Python walk against a five-second budget) and say so in the notes rather
   than silently truncating.
5. Before auto-repairing, refuse when a bridge would eat more than
   `max_fraction` (default 5%) of any one part's faces — that large, it is a
   mis-tag or a part that is genuinely fused by design, not a weld artifact,
   and deleting it would gouge the part.

## WHY

The rule that makes the tag-adjacency check safe to gate on is the anatomy
table, and the reason it exists is a measured false-positive rate: on
werewolf-wip-15, *"all 149 `Arm`/`Head` cross edges sit at z = 1.53–1.62 m on
`DEF-shoulder.*` and `DEF-spine.005/.006` — the trapezius seam, in one blob
per side. Calling that pair a bridge would fire 149 false positives on the
one mesh this gate exists for."* A blanket "any non-adjacent-tag edge is a
bridge" rule would have failed the one mesh whose actual trapezius seam is
legitimate.

## REJECTED

- **A blanket cross-tag-edge rule with no anatomy table** — measured to
  produce 149 false positives on a real, legitimate seam (the trapezius,
  where `Arm` and `Head` tags legitimately meet).
- **Gating on the spatial-outlier signal** (`BRIDGE_OUTLIER_FACTOR`) instead
  of reporting it — kept report-only by design: *"It is reported only and
  never turns the gate red."*

## SOURCE

- `addon/forge/tools/diagnose.py` — module docstring ("cross-part bridges"
  section, ~lines 29–34) and the `ANATOMY_ADJACENT` / `BRIDGE_OUTLIER_FACTOR`
  / `BRIDGE_VERTEX_LIMIT` / `BRIDGE_REPAIR_MAX_FRACTION` constants with their
  derivations (~lines 479–653).
