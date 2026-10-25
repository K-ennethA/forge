# shadowscale STATE (v2, 2026-10-17: symmetric rest pose)

- Version: v2 = v1 (Pixal3D sha 8633d29a, LIGHT finish) + light.yaw +41.5 deg (one rigid root turn) + bone repose (improve/proxy_finish_repose.py). DIGEST edc3303bd388f1a0 (twin identical). v1 colours / recolour3d / dense / floor_lift / AO unchanged (stage-1 boxes).
- Spec: design/review-log.md "2026-10-17 — Batch x15 v1 verdict" (rest pose symmetric, turns are posing); lane brief section V2 + UNIT V2 NOTE (symmetry pass only).
- Face measured on the v1 low (turned, not sculpted crooked): head z 5.4..6.95 best mirror plane yaw -41.5 deg, p99 0.146 u about its own plane (1.237 at x = 0); red eyes lateral -0.139 / +0.229 u, z 5.956 / 5.935; snout tip lateral +0.075 u. Both feet lay side by side on a -41 deg line.
- What v2 changed:
  - light.yaw +41.5 about (0, 0) to (-0.10, 0): head plane -> x +0.14, hip slabs -> x -0.06 (a 0.2 u lateral lean is left between them).
  - repose: legs symmetric match L (the planted upright +x leg; the -x leg was splayed out, ankle x -1.46), plant, plant_radius 0.8; arms symmetric mean (the -x arm was carried ~1.5 u forward across the chest); neck + head heading -y (0.0 deg: the root turn already squares the face); tail chain tail.0..2 heading +y (yaw 36 / 16 / 9 deg); head rigid box (2575 verts, max deviation from rigid 0.0 u).
  - rig redone in the turned frame (spine, arm points / points_R, leg points / points_R, 3-bone tail chain resting on the midline); focus boxes in the turned frame.
- Numbers: mirror p99 1.166 -> 0.881 (yaw) -> 0.442 u (repose, about the body plane x 0); edge stretch p99 2.28 (legs 2.32, arms 2.92, tail 2.37; max 15.1 at the front cloth between the arms); self-intersections 133 -> 1172 (979 in the moved region); feet planted (both 0.000 before centring; export lowest point is a coat spike at -0.024, so the talons sit 0.024 u up).
- Gates: runner GATE mean 0.665 (v1 0.742): back 0.632 (v1 0.637), side 0.697 (v1 0.846, FLAG: the sheet side matches the posed 3/4 body; the symmetric rest pose cannot); finish gate front 0.663 (proxy 0.851; the sheet front is 3/4), mesh 18000 tris, 0 open edges; cell_fit report-only (both checkers 1 failed).
- Open for the artist:
  - the export is centred on its bounding box, which a -x coat-fan spike (x -1.75 vs +1.48) pushes 0.134 u off the body plane: mirror p99 about the export origin is 0.636 u (0.442 about the body plane). The kit has no centring override.
  - the coat-train fan that swung to -x is pulled in with the -x leg, not mirrored; the +x side has no matching fan (never mirror-copied geometry).
  - stretch p99 2.28 is above azure_scale_mage's 1.48: the forward -x arm drags the front crimson cloth (arm match L was worse offline: p99 5.57).
  - movement + in-game height still open (clips PLACEHOLDER).

---
# v1 (kept for history)

# shadowscale STATE (v1 draft, 2026-10-16)

- Version: v1 = Pixal3D proxy (forced 3-view front + back + side, seed 56, sha 8633d29a), LIGHT finish (proxy_finish.py; config improve/shadowscale_finish.json). Geometry kept as generated: no symmetrise, no repose, no cuts, no parts, no glow.
- Spec: design/review-log.md "2026-10-16 — NEW UNITS x14" section SHADOWSCALE; sheet design/reference/shadowscale-character-sheet.png; intake design/reference/shadowscale/intake.json (+ intake_proxy.json).
- What the finish changed:
  - frame: fit_side_length Y x0.888 (proxy side length +12.6 %); floor moved from the tail-train tip to the front talons (0.090 u up), then x1.019 to the sheet front height 6.89 u (side 6.82, -1.0 %).
  - light.floor_lift: the train tip (x < -1.4) that sat 0.092 u below the new floor squashed onto z 0 (bottom 0.35 u, 1894 verts); the feet do not move.
  - tone (light.recolour3d, texture only): the near-black proxy plates rank-matched to the sheet (7 L* quantile bands x 2 a* halves, each to the sheet's median colour in the same band, x1.08 for AO; brightest first), crimson in 3 bands to the sheet crimson. Both red eyes are excluded by box (kept as generated). Basecolor L* p10/50/90 4.6/19.3/43.4 vs the sheet 5.7/19.8/46.6 (proxy 3.8/13.4/35.7); crimson share 4.4 % vs the sheet 6.0 %, median 93,33,50 vs 88,32,44.
  - dense: head box (helmet, snout, crest, eyes) vg_weight 0.1 + two eye boxes vg_weight 0: head share 0.287 of 18000 tris.
  - AO from the low (distance 0.2, fill below 0.04), bake_drop_interior 0.08, uv_island_margin 0.001.
- LOCKED: nothing yet (awaiting the artist).
- Knobs that matter: recolour3d targets / detail_gamma (tone; the AO factor 1.08); the crimson bands (cloth strength); body_tris 18000; dense vg_weight; refloor window; floor_lift ramp; clips (idle 72 f, breathe 1.2, walk 36 f, leg_swing 8).
- Open for the artist:
  - movement + in-game height (clips are PLACEHOLDER; the kit walk dips the feet 0.25 u below the floor).
  - pose as generated: the head is turned to -x, the legs are staggered (front foot y -0.8, rear foot y +0.8, rear talons 0.037 u up), and the train is swung. Mirror p99 is 1.17 u. The rig legs use the mean of both legs.
  - the back view reads 3/4: IoU 0.637 against the sheet back, which is the same as the raw proxy at 0.641. The proxy took the 3/4 turn of the front view. If a straight back is wanted, the proxy has to be regenerated (orchestrator).
  - the helmet was lifted with the body (texture only), so the head does not stay near-black against a gunmetal coat. Excluding it is a single exclude box in each recolour3d band.
- Unit gates: digest twins, bake gate, contract checks (cell_fit report-only), compose GATE lines, proxy_finish.py gate + panel (renders/shadowscale/shadowscale_finish_panel.png).
