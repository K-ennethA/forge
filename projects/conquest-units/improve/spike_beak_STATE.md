# spike_beak STATE (v2, 2026-10-17)

- Version: v2 = v1.1 + a symmetry pass only (artist 2026-10-17: "our characters should be symmetrical head turns are done through posing"). Every v1.1 change kept unchanged (light block identical: front-flap cuts, skin adds, recolours, back-of-head spike cut, colours). Config: improve/spike_beak_finish.json `repose` + `rig` (spine_why / leg why).
  - measured on the v1.1 low: the bird is NOT turned or off-centre (best mirror plane: whole low yaw 0.0 deg x 0.00; slabs z 0.3..3.0 yaw 0..0.5 x -0.01..0.01) -> no light.yaw. The face is NOT sculpted crooked: face p99 0.0157 u about its own plane (yaw +0.25 deg, x 0.005), eye band p99 0.015, pupils z 2.500 / 2.505, x -0.538 / +0.559; beak tip x +0.021, forehead spike tip x -0.002.
  - repose (bones, never mirror-averaging): legs one mirror pair at the mean + both feet planted (the +x foot floated 0.036 u; leg joint mirror error 0.048 / 0.068 / 0.027 u -> 0; edge stretch max 1.32 at the +x leg / belly junction, p999 1.15, toes untorn); face turned straight 0.25 deg (neck 0.13 / head 0.25) inside a rigid head box (5490 verts, dev 0.0 u). Back claws hover 0.085 u on both sides as generated.
  - no tail target: straightening the tail tip (slab x mean -0.02..-0.04) raised its mirror p99 0.078 -> 0.085 and tore the rump (stretch 1.52) offline: tip shape, not a swing.
  - mirror p99 (verts -> surface, x 0): build 0.053 -> 0.043; legs z < 0.9 0.065 -> 0.038; face 0.018 -> 0.015; the gate's vertex-to-vertex glb p99 0.102 -> 0.102 (its noise floor at 18000 tris). Gate mean IoU 0.932 -> 0.933. Image: renders/spike_beak/spike_beak_v2_symmetry.png.
  - remaining asymmetry: the proxy's own feather flaps on the upper -x back (max 0.19 u) and the tail tip, as generated.

# (v1.1, 2026-10-16)

- Version: v1.1 = v1 + the artist's v1 note "two wings  this one needs to be removed" (design/review-log.md "2026-10-16 — Spike Beak v1 note"): the FRONT wing flap behind the eye / cheek removed on both sides, the main tucked wing kept.
- v1.1 front flap removal (config light.cuts_front_flap_why / adds_why / recolour_why):
  - light.cuts: 48 boxes (+x 23, -x 25) in 0.03 x 0.05 cells over y -0.65..0.08, z 0.95..2.6 (eye cells z > 2.15, y < -0.38 never touched); proxy faces removed: +x 7772, -x 7252 (+ the back spike's 247).
  - the proxy body is a thin hollow hull (stage-1 occupancy 2.8 %), so a plain cut opened holes: the cut starts 0.15 u under the new skin and light.adds (285 capsules, r 0.1) rebuilds the skin. Skin = a per-side body quadric (fitted on the body outside the flap) + the body's own offset Laplace-filled from the surrounding real body: no ledge at the head, front or main-wing edge (median 0.08 u over the quadric; the measured skin under the free flap was 0.07).
  - light.recolour: 288 flat discs paint the new skin feather blue 88,136,200 (it has no proxy surface to bake from; run 7 baked it as a grey mean with magenta rims).
  - the back-spike nub: not present since v1 run 2 (cut floor 3.545 + erase3d); the head top reads as a flat feather plateau at z ~3.55, no yellow.
- v1: Pixal3D proxy (3-view, seed 56, sha f147ac02, gate reliable, mean IoU 0.947), LIGHT finish (proxy_finish.py; config improve/spike_beak_finish.json). Geometry kept as generated except the cuts: no symmetrise, no repose, no refloor, no glow (the sheet draws none).
- Spec: design/review-log.md "2026-10-16 — NEW UNITS x14" section SPIKE BEAK; sheet design/reference/spike_beak-character-sheet.png; intake design/reference/spike_beak/intake.json.
- Finish changes:
  - light.cuts: the proxy's SECOND spike on the back of the head (x -0.15..0.08, y 0.04..0.39, z 3.55..3.91) removed (orchestrator unit note: the sheet draws ONE forehead spike rising from the beak); box floor z 3.545 = the head dome beside it; 247 proxy faces left the bake source. The forehead spike (tip z 3.59, y -0.8), beak and eyes untouched.
  - light.erase3d: the cut spike's yellow skin left on the dome refilled from the surrounding feathers (sphere r 0.2, 11690 texels, fill 50,119,172 before the tone lift).
  - light.recolour3d "feathers": blue texels (L* 15..75, b* <= -12; pupils, eye whites, beak, spike, feet not selected) to 88,136,200 with the proxy's feather shading kept (gamma 0.8). Basecolor blue median 46,102,143 (L* 41.8, a* -4.5) -> 81,121,173 (L* 50.5, a* +2.0, b* -33.1); sheet side body 78,121,179 (L* 50.3, a* +3.6, b* -34.1).
  - light.ao_fill_below 0.04 (74632 of 1038597 AO texels: occluded bake origins between fused feather flaps).
  - light.dense: head / face box (vg 0.3) + both eyes + the forehead spike (vg 0): head boxes hold 55 % of the 18000 tris.
  - ao_from_low, ao_distance 0.45, bake_drop_interior 0.08 (frost_calf idioms).
- LOCKED: nothing yet (awaiting the artist on v2).
- Knobs that matter: light.recolour3d feathers target_rgb 88,136,200 + detail_gamma 0.8 (feather blue and contrast); light.cuts box floor z 3.545; body_tris 18000; dense boxes; ao_distance 0.45; clips (idle 72 f, breathe 1.5; walk 28 f, leg_swing 16, bob 0.02, roll 3.0).
- Open for the artist:
  - movement + height (clips PLACEHOLDER); build height 3.60 u (front-view spike tip removed with the back spike: the sheet front / back draw the spike to ~3.95 u, the side view to ~3.50 u; the build follows the side view).
  - a yellow strip runs down the chest below the beak (proxy as generated, x +-0.1, y -0.56..-0.83, z 1.0..1.8): Pixal3D's reading of the sheet FRONT view, where the long beak is drawn pointing down to z ~1.0; the side view draws no yellow on the chest. Left as generated (face area, light flow).
  - a blue fleck near the forehead spike tip and a cyan pupil highlight (sheet: white) are proxy texture, left as generated.
  - small near-black crevices on the rear wing / tail root in the side render: geometry pockets between the proxy's feather flaps (not albedo, not AO); visible now the feathers are lighter.
  - beak / feet yellow is the proxy's (basecolor yellow median 156,113,39, more orange than the sheet chips 236,206,129 / 221,176,89); not tinted.
  - mirror p99 0.103 u (glb, proxy as generated; build mirror p99 0.051).
  - no wing chain (tucked wings ride the chest / belly bones).
  - v1.1: the new flank skin is a smooth flat-painted patch (no feather texture, no proxy normal detail); a few dark normal-map flecks remain along its back edge next to the main wing (bake hits on the wing root); front IoU 0.951 -> 0.922 because the sheet FRONT view draws the side wings the flap was built from.
- Unit gates: digest twins, bake gate, contract checks (cell_fit report-only), compose GATE lines, proxy_finish.py gate + panel (renders/spike_beak/spike_beak_finish_panel.png).
