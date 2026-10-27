# vampwarrior STATE (v7.1 in place, 2026-10-26; awaiting the artist + the orchestrator's Codex review)

- v7.1 = v7 + the artist's two decisions after the Codex review of v7 (scratchpad codex_vamp7/review.md: body fixed, face /
  head hair not): (1) FACE-ONLY graft, (2) FULL sword posed in the clips; + cheap cleanups. Lane generator: session 4f1944ab
  scratchpad vampwarrior_lane2/gen_v71.py + clipgen.py + Q7.json (geometry judged on the stage-1 / crop path Q1..Q5;
  textured test builds Q4, Q6 at bake 2048/1024, AO 16; one full run).
  - HEAD: body6's own hair is kept EVERYWHERE (crown, parting ropes, face-framing locks, back). head6 (sha 0edb9cfc03f8)
    is grafted as a FACE MASK only: graft cuts remove everything behind y -0.36, beyond |x| 0.24, above z 7.885, the
    temple / forehead locks (|x| > 0.2 above 7.35, > 0.07 above 7.8, > 0.1 above 7.765), its neck below 7.15, plus v6.1's
    lash-plate trim. Scale 1.30 (= the sheet's iris spacing 0.255 u; v7's 1.40 was 8 % wider), irises z 7.62, nose tip
    y -0.77: 0.03 in front of body6's own face, so the mask lies ON body6's face and body6 needs almost no face cut (only
    its own small eyes, |x| < 0.17 in front of y -0.54, and its two floating lash capsules). Q1's box cuts of body6's face
    left a recessed forehead panel and ledges along the cheeks; the overlay removed them.
  - DENTS: with the face-only mask the cheeks read as broad even planes (no dents) on the same fine box / smooth region;
    v7's "dented cheeks" came from head6's own hair locks pressed over the cheek edges + their dark strokes, not the finish.
  - PAINT: head6 drawn lines lifted L* 14 -> 32 (lighter liner / brows), irises repainted 150,34,42; body hair -> warm
    ivory (196,184,170; Codex: cool white); body skin repaint extended to z 8.4 (body6's forehead around the mask was
    peach); despeckle: head6 lash streaks, nose-bridge / cheek strokes, body6's outer eye-corner lash paint, back-hair
    dark breaks (x +-0.4, z 5.1..5.65).
  - SWORD: the traced sword at FULL length (scale 1.0, full finial), point-down at axis (-1.50, -0.35), grip centre
    z 6.50, tip z 0.09. It rides its own bone sword.0 on the CHEST (shell false), not the hand: the rest / bind pose stays
    symmetric with both arms down and the sword stands on its tip beside her (bind-pose checks: feet_origin min z 0,
    tri budget, flat_shaded etc. pass; cell_fit report-only). The idle / walk clips are the kit's PLACEHOLDER motions
    re-keyed per frame as clips.extra (same rigkit formulas / chains / phases) with arm_r HELD: palm on the grip,
    elbow (-1.73, -0.11, 5.70), wrist (-1.69, -0.12, 6.53), elbow interior 41.5 deg (2-bone reach, clipgen.arm_hold).
    Axis x -1.50 keeps the sword inside the cape's width (Q4 at -1.75 made the bbox centring shift the unit +0.17 u).
  - RIG: falloff 0.06 -> 0.02 H (the broad default dragged the hip / thigh / cape out with the raised arm, Q4); new
    hair_side chain (the long side locks behind the arms) so they stay off the arm.
  - CLEANUPS: two hooked curl tips of the back hair trimmed (light.cuts boxes at x +0.53 / -0.59, z 4.92..5.12); the
    neck is body6's own (head6's neck cut), throat repainted skin.
  - Run: DIGEST 9636af4f9a284c1f, twins identical, bake diff 0. Checks 7/1 + 11/1 (cell_fit only, report-only). 48624
    tris (shell 42000 + sword 6624); face box 2156, head box 9621, back-hair box 11329, right hand 1605. Build open edges
    0 (glb weld 186 / 65 = the sword pieces, as v5.x). FINE_STITCH head 673642 / back 1379326 fine tris, stitched 984 /
    3282, failed 0, open 0, non-manifold 0; FINE_REBALANCE reference 0.118 / calibrated 0.186 / got 0.113. Bake normal
    61.4 % / albedo 63.9 %. low->proxy p99 0.044, proxy->low p99 0.060. Clips idle / walk seam 0, clearance -0.007 /
    -0.011 u. Mirror p99 0.75 (the sword). Centre shift x -0.047 (the sword's tassel still widens the bbox a little).
    GATE front 0.783 (proxy 0.724) / back 0.723 (0.738) / side 0.683 (0.697), mean 0.730, flagged none; kit gate mean
    0.740. GATE bake_reference=none (compose line, as v7).
  - Renders: renders/vampwarrior/vampwarrior_v71_sheet_v7_v71.png, _v71_face.png (sheet panel | v7.1 / v7 at 0 / 35 /
    90), _v71_back.png, _v71_sword_hold.png (idle frame 1 + the bind pose), _v71_cu_*.png.
  - Open (artist reviews before any rebuild): head6's eyes still read dark / narrow (black lash shapes), a faint nose
    stripe; body6's rope ends at both temples are small knots with a little dark paint; face box only 2156 tris (the
    fine-box share fell with the smaller head6 volume); more hooked curl tips remain at the side locks; a few dark breaks
    in the lower back hair; the raised arm lifts a cape / sleeve flap from the elbow toward the hip in the idle; the
    unit sits 0.047 u off centre; the profile (90 deg) is mostly hidden by body6's face-framing ropes.

## v7 (2026-10-26; superseded by v7.1)

- v7 = a FULL re-generation (artist chose it after v6.1 still read "clumpy, like poorly sculpted"): a new body proxy +
  head6 graft + the traced sword. Config improve/vampwarrior_finish.json (lane generator: session 4f1944ab scratchpad
  vampwarrior_lane2/gen_v7.py + P11.json; backups of the v6.1 config / parts / palette / intake in vampwarrior_lane2/v5_backup/).
  - BODY: design/reference/vampwarrior/proxy_v6/vamp_body6_s11.glb (sha 728b5cea, Pixal3D 4096 of the Codex full-body
    turnaround codex_body_v6/: symmetric rest pose, arms down, no sword, flat-soled boots, hair to the hips, cape behind).
    intake.json `proxy` block replaced by its sheet_proxy score (mean IoU 0.717 "unreliable": the sheet holds a sword with a
    raised arm and draws heels; it is the base mesh, not a depth reference); the old block kept as `proxy_v5`. midline_x
    -0.01. No repose (generated symmetric); mirror p99 0.39 u (sword).
  - HEAD: head6 (sha 0edb9cfc03f8) at scale 1.40 (v6.1 1.27): body6's own head size (its eye spacing 0.28 u; at 1.27
    head6 sat 0.12..0.17 u inside body6's back-of-head hair). Irises z 7.62 (body6 eye line), neck centre on body6's neck
    (y -0.25), nose tip y -0.78. Body6's own BACK-OF-HEAD HAIR IS KEPT (light.cuts remove only its face / front of head
    above z 7.36 in front of y -0.1, its chin / jaw front, and its face-framing strands z 7.0..7.36): the long back hair
    is continuous from the crown down, so there is no join line at the back. Body6's neck kept under head6's (their
    fronts coincide); its black throat texels repainted head6's skin (recolour3d neck_skin).
  - PAINT (texture only): head6 tone (skin L* 72 grey-mauve, hair -> L* 82 / a 3 / b 2.5 = body6's hair, lips dark,
    the L* 65 green tint removed); red irises (light.recolour cylinders, 150,34,42); despeckle (frac 0.85) on head6's
    lash streaks past the outer eye corner, hair strand strokes (crown, forehead locks, side / face-framing locks),
    nose-bridge and cheek strokes. Body: yellow-green trim -> grey silver (115,113,117), red lining (a* > 12) ->
    restrained burgundy (74,40,46), peach skin -> grey-mauve (180,168,166). ao_distance 0.3 -> 0.15 (darker crease
    streaks in the hair). Smooth regions skin_face / hair_head / hair_back (palette region hair_back added).
  - SWORD: the v5.1 traced sword scaled x0.60 about its grip into the LOWERED right hand (tip z 0.20); the old
    board-sword cuts dropped. DEVIATION: the sheet's sword is nearly her height but is held with a raised arm; at full
    length in the lowered hand its tip would sit 2.4 u below the floor. The leaf finial above the grip is shortened to
    z 4.18..4.42 (inside the fist; a full one ran into the forearm). Options for the artist: keep / raise the arm by
    repose (asymmetric rest pose) / a pose in the clips.
  - BOOTS: body6's own flat boots (the old wedge_foot parts and foot cuts dropped). Old cape recolour, leg repose and the
    ellipsoid hair sheet dropped.
  - light.fine: head [[-0.75,-0.92,6.75],[0.75,0.62,8.3]] div 2 + back_hair [[-1.5,0.0,4.6],[1.5,0.95,6.6]] div 2
    (not touching). FINE_STITCH head 1025310 / back_hair 1383002 fine tris, stitched 984 / 3282, failed 0, open 0,
    non-manifold 0. FINE_REBALANCE reference 0.205 / calibrated 0.341 / got 0.201.
  - Run: DIGEST 328cdef8eb1d1e12, twins identical, bake diff 0. Checks 7/1 + 11/1 (cell_fit only, report-only). 48624
    tris (shell 42000 + sword 6624; hero 30-50k); face box 5961, head box 15806, back-hair box 8569, right hand 3661.
    Build open edges 0 (glb weld 186 open / 65 non-manifold: the closed sword pieces after the glTF split, as v5.x).
    low->proxy p99 0.043, proxy->low p99 0.075. Bake normal 63.2 % / albedo 64.9 %, cage 0.225. UV face 569 texels/u.
    GATE front 0.737 (proxy 0.724) / back 0.750 (0.738) / side 0.679 (0.697), mean 0.722, flagged none; kit gate mean
    0.733 (proxy 0.720), finished-vs-proxy 0.961. GATE bake_reference=none (compose line; same build path as v6.1).
  - Renders: renders/vampwarrior/vampwarrior_v7_sheet_v61_v7.png (sheet | v6.1 | v7 front / side / back),
    vampwarrior_v7_back.png, vampwarrior_v7_face.png (sheet face panel | v7 / v6.1 at 0 / 35 / 90), vampwarrior_v7_cu_*.png.
    Paint-step close-ups (P7 tone -> P9 irises -> P10 hair despeckle -> final): lane scratch r_P7 / r_P9 / r_P10 / r_final.
  - Open (not fixed, the artist reviews first): head6's hair still carries grey strand strokes / crease shading (lighter
    than v6.1, not gone); heavy black lash plates (liner wing); the 90-degree profile is hidden behind head6's side locks;
    head6's front lock ends meet body6's strands at z ~6.9 with a visible step; a few dark specks in the lower back hair;
    sword length (above); the lower cape keeps many thin strips.

## v6.1 (2026-10-25; superseded by v7)

- v6.1: head5 is replaced by head6 because the artist rejected head5's face: "the lips are far too wide and dont match", "the side
  looks like 2 faces overlayed on one". head6 = head_graft/head6/head6_s56_pixal3d.glb (sha 0edb9cfc03f8, Pixal3D of
  codex_refs_v3: head + hair + short neck only, landmark-aligned, right = mirrored left). Codex review: one face, mouth
  0.59 x pupil spacing (sheet 0.60).
  - Graft: scale 1.27, irises z 7.66, nose y -0.61. shell_u is kept (head6 is thin-walled, 776 open edges).
  - Proxy cuts: the proxy head is cut above z 7.36, and the collar front is rounded down under the jaw.
  - Paint: a graft cut removes the lash plates. Darks are lifted, the lips are dark instead of pink, and despeckle
    capsules cover the lash streaks, the temple knot and the hair strokes.
  - Back hair: a flattened-ellipsoid hair sheet (try B) over the proxy's tube cascade. The back-hair fine box stops at
    z 6.69, because touching fine boxes leave open edges at the stitch.
  - Run: DIGEST 15ddd4c5e8276877, twins identical, bake diff 0. Checks 7/1 + 11/1 (cell_fit only). GATE front 0.824 /
    back 0.635 / side 0.860, mean 0.773.
  - NOT yet reviewed visually: the textured renders, the paint before / after close-ups and the back panel are still to
    do. Lane scratch: session 4f1944ab scratchpad\vampwarrior_lane\v61\ (generator gen_v61.py + B66.json; the
    complete run copy is in run_outputs\).
- v6 (below) is superseded; it used head5.

- v6 = the head-only re-run (artist 2026-10-25 "clumpy like poorly sculpted" -> "make the fixes"; handoff step 2). The v5
  body is kept (proxy 3f0ae1b3, hero tier body_tris 37000, traced sword, low wedge flat boots + cuts, cape lining
  recolour, leg repose, skin_ / hair_ smooth regions, face UV island); the WHOLE head incl. hair is head5.
  - HEAD: head5_pixal3d.glb (sha 7d5be5b4b64a, 4-view Pixal3D of codex_refs_v2) grafted frontal, yaw 90, mirror plane glb
    x 0.001 (iris blobs -0.0876 / +0.0895), scale 1.38 (between face measures 1.44-1.46 and skull measures 1.11-1.23),
    irises z 7.66, crown 8.14, neck centre y -0.17. Kept WHOLE (no back / side clip, no taper, no stump) incl. its own
    symmetric high collar + throat above the join z 7.0; light.head_graft.cuts remove its bust (front below 7.0, shoulders
    behind |x| > 0.45, shoulder tops |x| > 0.62). Tone L* 86 -> 76, b* -4, a* +1; red irises L* 52 -> 49 (darker).
  - PROXY HEAD REMOVED: light.cuts above z 7.0 within |x| 0.62 and above z 7.18 out to |x| 0.95 (the turned head cap =
    the diagonal skull outline, face-framing hair, side hair, the turned collar tips). The proxy hair below (back cascade
    and the strand over her left shoulder) stays: head5's hair ends at the shoulder blades, the sheet's at the hips.
  - light.fine: one box over the head [[-0.72,-0.8,6.8],[0.8,0.8,8.35]], div 2 (step 0.0136 u); dense: face (density 6,
    vg 0) + both hands; the v5 head / face / strand dense boxes dropped.
  - DROPPED: paint_project face re-fit, despeckle hair darks, pink eye recolours. RE-ADDED one: light.despeckle on the
    two temple locks only (head5's drawn strand lines beside the brows; 2740 albedo / 196 AO texels refilled; before /
    after: lane scratch r/despeckle_before_after.png).
  - KIT PATCH (lane scratch kit_patch/head_graft_shell_cuts.patch, awaiting the orchestrator): head_graft `shell_u`
    (thin / hollow head walls sampled consistently on the fine grid; without it the fine grid aliases head5's walls into
    a lattice of pits) and `cuts` (graft volumes removed + their bake faces). Absent keys = unchanged (drakescale_beast
    head_graft outputs byte-identical unpatched vs patched). THE CONFIG IN PLACE USES BOTH KEYS: an unpatched run
    silently ignores them and gives the pitted head with head5's bust.
  - Scratch build with the patch (lane scratch sroot/, the project outputs untouched; numbers to confirm on the official
    run): 44968 tris (shell 37000 + sword 6624 + boots 2 x 672), build open edges 0; face box 3569 tris, head box 11606
    (v5.3: 7171 / 12548 on the lumpy remesh); FINE_STITCH fine_tris 765602, stitched 1092, failed 0, open 0,
    non-manifold 0; FINE_REBALANCE reference 0.157 / calibrated 0.351 / got 0.155; bake normal 61.9 % / albedo 64.1 %;
    UV face 550 texels per u; checks 7/1 + 11/1 (cell_fit only); kit gate front 0.826 / back 0.622 / side 0.864, mean
    0.771 (v5.3 0.828 / 0.622 / 0.870, mean 0.773); low->proxy p99 0.045, proxy->low p99 0.060.
  - Open: head5's eyes are narrow with thick liner (sheet: large round red eyes); the face-framing locks flare like a hood;
    the head5 hair ends in a ragged edge at the shoulder blades over the proxy cascade, with two pointed lock tips at the
    back; a small dark lock line at her right jaw.
- v5.3 numbers beside: DIGEST 9d9bb8ee724adf74; 44968 tris; GATE (runner) front 0.823 / back 0.635 / side 0.863, mean
  0.774; kit gate mean 0.773; face box 7171 tris.

## v5.3 (2026-10-25)

- v5.3 = v5.2 + the artist's 2026-10-25 note ("the front render for the face looks good, has dark spots that should be
  fixed but the side is now off") + the Codex review (design/codex-review-2026-10-25-duskmaw-vampwarrior.md); kit patch
  head_graft.clip_q2 (lane, applied by the orchestrator; vulture regression 5b3aaa03830de189 unchanged):
  - HEAD: head4_pixal3d.glb (sha beed58b38da4; 4-view Pixal3D of codex_refs/head_*.png) replaces head192d: frontal
    (yaw 90, mirror plane x 0.000), scale 1.52 (iris spacing), irises (+-0.113, -0.47, 7.745), mouth z 7.523; back cut at
    glb z -0.05 + taper 0.25 u, SIDES cut at glb |x| 0.28 (clip_q2), neck / collar capped (stump q1 -0.13); tone L* 84 ->
    75, b* -8. The proxy face front cut widened (x +-0.42, y < -0.30) + the proxy's side hair in FRONT of both ears cut
    (|x| 0.33..0.80, y < -0.12, z 7.25..8.2; artist-approved): the 35 / 55 / 90 deg views read as a head in profile.
  - FRONT kept: paint_project of the sheet face panel re-fitted to head4's landmarks; darks lifted (L* 20 -> 26, 35 -> 42:
    softer mouth surround), ellipse r 68 x 84 px; face AO blur 6 passes.
  - Hair streaks: light.despeckle on the crown, back of head, side hair behind the ears and both cheek edges (texels
    < 0.72 x the region median refilled from lighter neighbours: 3380 albedo / 5803 AO texels). Some strand lines and the
    lumpy framing strands remain.
  - FEET: low wedge flat feet (parts wedge_foot: continuous flat sole, top 0.30 u at the ankle -> 0.10 at the toe, no heel
    step, 0.96 x 0.36 u, near-black) + an oval ankle collar (0.22 -> 0.16 u, to z 0.85); placed under the shins where
    they really end after the repose (+-0.80, -0.39) so both legs run continuously into the boots.
  - Run: DIGEST 9d9bb8ee724adf74 twins identical, bake diff 0; checks cell_fit only; 44968 tris (shell 37000 + sword
    6624 + boots 2 x 672); UV face 572 / strands 500 texels per u, coverage 23.4 %; GATE front 0.823 (FLAG as v5.2) /
    back 0.635 / side 0.863, mean 0.774; walk clearance -0.058 u.
  - Renders: vampwarrior_face_closeup.png (sheet panel + codex side ref | front / 35 / 55 / 90), vampwarrior_face_v52_vs_v53.png,
    vampwarrior_feet_closeup.png, vampwarrior_finish_panel.png.


- v5.2 = v5.1 + the artist's 2026-10-25 asks (design/review-log.md "2026-10-25 — Vampire Warrior v5.1 face + feet":
  "why is it look unpolished and choppy" / "can we just make a flat foot like our regular old model"), on the lane's
  kit patch (applied by the orchestrator; vulture_creature regression digest 5b3aaa03830de189 unchanged):
  - light.uv (SMART method, the orchestrator's pick: it passes uv_health): the face and both front hair strands each one
    front-projected island (front-facing faces), face x4 / strands x3.5 the rest's density, margin 0.0015, neighbour
    dilation 24 px (no global-mean bleed). Islands 6728 -> 6780, coverage 27.0 -> 22.2 %, face 554 texels per u (v5.1
    mesh average 182), strands 485.
  - light.smooth: skin_face / hair_head / hair_front (12421 faces) smooth-shaded, their normal texels neutral and AO
    softened (3 passes); SMOOTH_OK_PREFIX ("hair", "skin"); the rest flat. flat_shaded passes.
  - light.paint_project: the sheet face panel projected onto the grafted face (affine on the irises + mouth, 300 px per
    u; inner-face ellipse r 62 x 78 px, feather 14), toned L* 52 -> 70 warm grey-mauve, darks kept: crisp brows, red
    irises with pupils and lid lines, dark lips, two fangs under the upper lip. Graft hair L* 68 -> 78. The eye-corner
    magenta repainted (light.recolour); a faint pink trace remains at her left temple / under the chin.
  - FLAT FEET: the proxy's pointed heeled feet cut below the ankle (light.cuts), closed flat boot feet (parts
    boot_foot: flat sole, rounded toe, 0.98 x 0.38 u, near-black boot region, shaft r 0.25 to z 1.05) at (+-0.87, -0.38),
    rigid on the nearest leg bone; leg chain re-jointed to the cut shin ends, plant_radius 1.6: both shin ends planted
    (foot min z after 0 / 0). The -x armoured shin plate still shows a little at the front of the shaft.
  - clips leg_swing 8 -> 5 (walk dip 0.077 -> 0.053 u).
  - Run: DIGEST 657232cf754294ff twins identical, bake diff 0; checks 7/1 + 11/1 (cell_fit only; uv_health 0 zero-area /
    0 flipped); 44688 tris (body 37000 + sword 6624 + boots 2 x 532); GATE front 0.823 (proxy 0.894, FLAG: leg repose +
    traced sword) / back 0.636 / side 0.864, mean 0.774; bake coverage normal 63 % / albedo 66 %.
  - Renders: vampwarrior_face_closeup.png (panel | front | 3/4 | 55 deg), vampwarrior_face_v51_vs_v52.png,
    vampwarrior_feet_closeup.png (front | side), vampwarrior_finish_panel.png.
- v5.1 (below) still describes the head graft, sword and tier.


- Version: v5.1 = the v5 Pixal3D LIGHT flow at HERO tier (artist 2026-10-24 v5 verdict "like a low render of what it
  should be": keep the v5 clothes / shape, raise the fidelity). v5 (2026-10-24) replaced the procedural v4.2 MPFB build
  (git 0a6da6f / 6fd5808). Config improve/vampwarrior_finish.json, stub improve/vampwarrior_build.py, parts
  improve/vampwarrior_parts.py, palette palettes/vampwarrior/default.json (proxy_albedo white + sword_* regions).
- Proxy: sha 3f0ae1b3 (4096 texture, 400k faces; same views / seed / shape as v5's b8352fbf, kept in proxy_v5_2k/).
  Used as is (no pre-decimation): stage 1 49..64 s, peak ~2.0 GB (python); build / twin Blender peak ~4.3 GB each;
  full runner ~5.5 min. One build died once with an out-of-memory 4096 normal bake while another lane was heavy
  (rerun clean).
- Spec: design/review-log.md "2026-10-24 — NEW UNIT: Vampire Warrior re-run" + "2026-10-24 — Vampire Warrior v5 verdict".
- Tier: hero [30000, 50000]; body_tris 37000 + sword 6624 = 43624. Bakes: albedo 4096, normal 4096, AO 2048. Dense
  boxes: head, grafted face (x3), both front hair strands, both hands.
- HEAD (light.head_graft): the orchestrator's single-view Pixal3D of the sheet face panel (head_graft/head_pixal3d.glb,
  sha 192d4a91f114) grafted FRONTAL: yaw 90 (glb face +z), mirror plane x +0.010 (mirror search), scale 1.06, place
  (y -0.179, z 7.605) = the proxy's eye height / spacing; back clipped (clip_q0 0.10) + tapered 0.22 u into the proxy
  head, neck / collar below the chin capped (stump q1 -0.30); the proxy's soft face front cut (light.cuts box x +-0.34,
  y < -0.40, z 7.32..8.25), the proxy's long hair kept. Tone: L* 75.5 -> 73.5, a* +1.2, b* -5.5 (warm pale grey-mauve
  between body and sheet). Paint on the baked albedo (light.recolour): red irises 100,34,40 + dark pupils (the graft's
  eyes are dark brown), the graft texture's magenta patches at the eye / mouth corners repainted skin.
  The v5 head-turn repose is DROPPED (the graft has no body yaw; turning the head would turn the frontal graft 30 deg);
  the proxy hair cap stays as generated. Legs repose kept (mirror-symmetric at the mean, both feet planted).
- SWORD (light.parts, kit patch applied by the orchestrator): sword_part_v51b. Blade + finial OUTLINES traced from the
  sheet SWORD DETAIL mask (lane scratch trace_sword.py: Moore trace + Douglas-Peucker 0.8 px; blade rows 462..907,
  finial rows 62..352), constrained-Delaunay triangulated, lens section thinning to the edges, dark centre vein, width
  x 0.75 (= the front view's blade width), neck narrowed below the guard; silver guard ring z 5.45, mid-blade ring
  z 3.57, near-black wrapped handle with silver rings, four flat red ribbons from under the hand; point-down at x -1.72,
  tip z 0.23; rigid on bone sword.0 (parent arm_r.2). The board-sword cuts as in the v5 sandbox.
- v5.1 run: DIGEST a75b8a2a70e741cc twins identical, bake diff 0 texels (normal 16.8 M, AO 4.2 M); checks 7/1 + 11/1
  (cell_fit only, report-only); 43624 tris, 0 open edges (build); low->proxy p99 0.045, proxy->low p99 0.064 u; bake
  coverage normal 71 % / albedo 77 % (the sword's UV islands are not baked: palette material); GATE front 0.834
  (proxy 0.894, FLAG = the symmetric-leg repose + the thinner traced sword) / back 0.637 (0.685) / side 0.865 (0.864),
  mean 0.779; mirror p99 1.11 u (raised sword arm + sword); repose stretch p99 1.20 / max 1.91; clips walk clearance
  -0.028 u. Gate glb weld: 186 open / 65 non-manifold welded edges (coincident vertices between the closed sword
  pieces after the glTF split; the build mesh is closed) - not chased.
- Renders: renders/vampwarrior/ (views, ortho, focus, clips, sheet_vs_model, finish_panel, face_closeup, sword_closeup).
- LOCKED: nothing yet (awaiting the artist).
- Knobs: head_graft scale / place / clip_q0 / taper / stump / tone; light.recolour iris colour 100,34,40, pupil, pink
  patches; light.cuts face box; sword args (width_scale 0.75, blade_neck, thick 0.04, ring_z, ribbons) + palette
  sword_*; body_tris 37000; dense boxes; repose legs; clips (placeholder).
- Open for the artist: movement (clips PLACEHOLDER); graft side profile is the panel block's flat side (55 deg view);
  graft hair (L* 68) darker than the proxy hair (L* ~80); proxy back cape red-brown on its lower outer half; skin kept
  warm pale (graft toned toward grey-mauve).
- Kit gaps (reported, not patched): head_graft has no body yaw (a turned proxy head cannot keep its repose with a
  frontal graft); _graft_tone has no a* clamp (the graft's magenta patches needed geometry-masked repaints).
