# frost_giant_child STATE (v2, 2026-10-17)

- Version: v2 = the Pixal3D proxy (seed 56, sha 0edeb461), LIGHT finish (v1.1, almond eyes, unchanged) + SYMMETRIC REST POSE (light.yaw root turn + bone repose; config improve/frost_giant_child_finish.json). DIGEST 9d3b0787ba9eaed3 (twin identical, bake diff 0 texels). v1.1 was DIGEST 187c09d3bcce6fe0.
- Spec: design/review-log.md "2026-10-17 — Batch x15 v1 verdict" (artist named this unit: symmetric rest pose; head turns are poses).
- v2 changes (geometry never mirror-averaged or re-sculpted):
  - Finding: Pixal3D built the WHOLE child turned about Z. The best mirror plane of every height slab sits 22..26 deg off X = 0 (feet 22, shins 24, hips 22, belly 26, chest 26, neck 20, head 22, hands 26). The v1 "yawed head", "swung arms" and "staggered legs" are one root turn.
  - light.yaw -23.5 deg about (0, 0.2): one rigid turn of the whole low (opt-in added to proxy_finish_light.py by the raven_shade lane; uncommitted - commit together). Mirror p99 1.062 -> 0.261.
  - repose in the turned frame: legs symmetric (mean) + planted. The +x foot hung 0.099 u above the floor and is now planted (glb foot min z +x 0.012, -x 0.000). Arms symmetric (mean): the joint error was 0.12..0.19 u and is now 0. Neck / head heading -y with distribute (neck +2.07, head +4.14 deg): the iris-centre line is now exactly level (yaw 0.00 deg). Rigid head box: 5162 verts, max deviation from rigid 0.0, so the v1.1 eye paint is exact.
  - Mirror p99 0.249 after the repose (plane x 0) and 0.282 final. The final centring is bbox-based (shift x -0.044) because the -x arm crystal reaches 0.09 u further out.
  - Edge stretch p99 1.063, max 1.444 (6 edges > 1.25, all at the neck / shoulder z 4.33). Legs max 1.20. Self-intersections 3 -> 4.
  - Evidence: renders/frost_giant_child/frost_giant_child_v2_symmetry.png (front ortho v1 vs v2, midline drawn).
- Gates: contract checks 7 / 1 failed and 11 / 1 failed (cell_fit only, report-only). Open edges 0, 18000 tris.
  - Sheet gate (compose): front 0.810, side 0.719, mean 0.764, both FLAGGED (v1 0.895 / 0.881 / 0.888). finish gate mean 0.775 (v1 0.882).
  - The drop is the pose, not the shape: the sheet's front / side are drawn turned. The v2 glb turned back +23.5 deg scores front 0.875, back 0.858, side 0.878, mean 0.870.
  - GATE bake_reference=none is the known stub-build kit gap.
- Knobs that matter: light.yaw deg -23.5 (the -22..-25 sweep is within 0.01 p99); repose falloff 0.03 (legs / arms); rigid head box z >= 4.55, margin 0.25. The v1.1 knobs below still apply.
- Open for the artist (v2):
  - The face sits 0.10 u to -x of the body midline (eye midpoint x -0.098), and the +x eye is 0.10 u higher than the -x eye. Pixal3D sculpted it that way; bones can only turn the head, so it is left as generated.
  - The arms are a mirror pair at the joints, but the two arm crystals differ in shape (arm mirror p99 0.47 u).
  - Panel face row: the raw-proxy column is framed with the head yaw only (4.14 deg), not the root turn, so the proxy face shows turned (panel kit gap; the finished column is head-on).
  - The rig's R-side chains are mirrored about the bbox centre: they sit up to 0.09 u off the -x limbs.

## v1.1 record (kept below)

# frost_giant_child STATE (v1 draft, 2026-10-16) - superseded by v2 above

- Version: v1 = the Pixal3D proxy (seed 56, sha 0edeb461; proxy gate reliable, mean IoU 0.897), LIGHT finish (proxy_finish.py; config improve/frost_giant_child_finish.json). Geometry is kept as generated: no symmetrise, repose, straighten, cuts or parts, and the face is untouched. DIGEST 187c09d3bcce6fe0 (twin identical; v1.1 = the eyes-only follow-up).
- Spec: design/review-log.md "2026-10-16 — NEW UNITS x14" (FROST GIANT CHILD); sheet design/reference/frost_giant_child-character-sheet.png; intake design/reference/frost_giant_child/intake.json.
- What the finish changed:
  - frame: the refloor window only confirms that sheet_proxy already floored the feet (z0 = 0). It rescales x1.0102 to the sheet front height 6.65.
  - decimation: 18000 tris. The head box is held at 2.5x density (vg_weight 0.3) and the two eye-socket boxes are held denser still (vg_weight 0). The boxes hold a 0.58 share of the tris (target 0.48).
  - colour (texture only, light.recolour3d): the dark teal in the proxy panel is mostly the panel light. The proxy texture itself is light blue but darker than the sheet (median 133,177,222 vs 171,208,240), and its pale facets have a cyan cast.
    - pale_ice: pale texels (b* > -15) lifted to 222,236,244.
    - blue_ice: blue texels lifted to 150,194,244.
    - Both use detail_gamma 0.85 with a 10-unit b* blend band. Run 1 at gamma 0.5 / 0.6 posterised the ice into flat blotches.
    - Result: basecolor pale 211,226,235 vs sheet 213,231,239; basecolor blue 142,182,228 vs sheet 145,189,241.
    - eyes (v1.1, coordinator gate: v1's painted eyes read as cloudy white blotches): each socket opening was traced on a z-buffered render of the proxy looking straight into the socket and smoothed into a clean almond outline. The outline is tiled by spheres that never reach past it.
      - eye_rim: the opening + 0.01 u is painted the sheet's outline blue 47,88,168 (19123 texels).
      - eye_iris: the opening eroded by a 0.035 u rim is painted the pale iris 196,234,246 (10967 texels). There is no colour select, so the edge has no blotches.
  - glow: the low faces inside the iris outline (eroded a further 0.01 u) go to the palette region eye_glow: 1484 faces, 0.45 % of the area. Its rgb is white and its emission is 194,233,243 x 0.7. The rim stays unlit and nothing else glows.
  - eye close-up: renders/frost_giant_child/frost_giant_child_eyes_closeup.png (sheet vs build, front / side / 3/4).
  - bakes: ao_from_low (64 samples, ao_distance 0.45), bake_drop_interior 0.08 (23652 tris dropped), flat reference. Normal coverage 98.1 %, albedo coverage 91.5 %.
- Rig: spine hips / spine / chest / neck / head; arms (kind arm, 3 bones) and legs (kind leg, 3 bones). Each joint is the mean of the proxy's +x limb and the mirrored -x limb. 18 bones.
- LOCKED: nothing yet (awaiting the artist).
- Knobs that matter: light.recolour3d targets (pale 222,236,244 / blue 150,194,244, detail_gamma 0.85, b* split -15 with soft 10; eye rim 47,88,168 / iris 196,234,246, rim width 0.035 u; the eye_rim / eye_iris / glow sphere lists (generated from the traced socket outlines; reshaping an eye means re-tracing and regenerating them)); palettes/frost_giant_child/default.json eye_glow emission_scale 0.7; ao_distance 0.45; body_tris 18000; dense density 2.5; clips (PLACEHOLDER).
- Open for the artist:
  - movement + in-game height: the clips are PLACEHOLDERS. The kit walk dips the feet 0.124 u below the floor.
  - Left as generated, asymmetric: mirror p99 1.06 u (mean 0.18).
    - The head is yawed about 23 deg toward its right (-x): the eye pair runs along (1.02, 0.44) in xy. The sheet's front view is also drawn slightly turned.
    - The arms swing: +x back y +0.75..1.2, -x forward y -0.4..-0.8.
    - The legs are staggered about 0.7 u in y.
  - The proxy grows more spikes on the crown than the sheet's two horns, so the head outline is wider and spikier at the top. Left as generated (no cuts on the head).
  - The sheet's small nose / mouth dot is not visible on the proxy face (face as generated).
  - The ice is opaque: it has no translucency or refraction (the sheet's "translucent ice surface").
  - The scaffold renders light the model darker / greyer than the basecolor (judge the tone from improved/textures/frost_giant_child_basecolor.png).
- Unit gates: digest twins, bake gate, contract checks (cell_fit report-only), compose GATE lines (bake_reference=none is the known stub-build kit gap), proxy_finish.py gate + panel (renders/frost_giant_child/frost_giant_child_finish_panel.png).
- Open artist questions: design/OPEN-QUESTIONS.md section 'frost_giant_child'.
