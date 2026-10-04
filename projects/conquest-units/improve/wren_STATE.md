# Wren â€” unit state (orchestrator-maintained; briefs point here instead of restating history)

- **Current:** v6.1 (lips + wider mouth) on v7 base, fork dropped (8b7bce7), + 2026-10-04 fists / lower run knee / glb
  loop keys (entry below; pending commit; pouch side effect open).
- **LOCKED (artist-approved, do not regress):** body/outfit/clips below the neck (v1);
  eye SOCKET scale x1.30 ("perfect"); iris at ~58% coverage; flat under-eye (v5 fill);
  one unified mouth line at Ashe ratios (v6) + subtle lip volumes, paler tint, 44 mm width (v6.1); side part on his left; ribbon locks +
  layer resolve + tuck shade (v7); HAIRSTABLE decoupling (face/mouth edits must never
  move hair â€” 4-case runner gate).
- **Style bar:** FE/anime â€” refs in design/reference/fe-style/ (archer figure = hair
  + mouth bar; Ashe portrait = face ratios) and anime-3d/ (Alicia = construction).
- **Hair normals (research H5, 2026-10-03, pending commit):** HAIR_NORMAL_CARRIER="vertex" -- proxy-leaned normals ship
  as custom split normals on the hair regions (smooth), hair strip of the normal map flat; body/outfit flat and glb
  rows byte-identical to v6.1. Contract flat_shaded exempts only `conquest_smooth_regions` (hair-family, checker-capped).
  "map" restores the v6.1 bake. Paint tiers still read blocky (per-face paint, not shading).
- **Run clip (2026-10-03, pending commit; spec review-log "young hero sprint"; Wren is MALE):** "run" = 12 f / 0.5 s,
  240 steps/min (1 step = 2.08 game cells at 0.12 s/cell), stride 2.33 m @ 4.67 m/s, zero toe slip, flight f5-6 / f11-12,
  head stabilised, left-arm pump + cloak-wrap coupling, fork carried upright (walk convention). Knobs RUN_* / FORK_RUN
  (top of wren_build.py). idle/walk glb sampler data byte-identical to v6.1 (proved). Preview renders/wren/wren_run_preview.png
  (runner: render_runstrip + compose --run-preview). Contract clip_names needs "run" in rigkit.ALLOWED_CLIP_NAMES (outside
  the lane). KNOWN: run torso_outfit cloak crossings 24 = the s7 sash weight defect (12 sash verts/part at 1.0 on the
  forearms; the strand is visible in the approved walk too) -- s7 fix queued, not a clip issue.
- **s7 weight-source lane (2026-10-03, STOPPED - needs s3):** knobs TORSO_HUNG / CLOAK_FOREARM / CLOAK_HIP added to
  wren_build.py + s7, shipped OFF (build digest c9215939245d39b5 reproduced, every part). With them ON: every cloak crossing
  0 in idle / walk / run (run torso 24 -> 0), keys byte-identical, sash stretch strand gone -- BUT the root cause is s3
  GEOMETRY: BVH_SASH (comb_bvh, whole body incl. the A-pose hands) shapes the sash rings and places the pouch, so one ring
  per wrap sits at the rest hand (344-380 mm radius vs ~140-180) and the pouch sits ON the hand (x 0.32-0.40, phi 58).
  Trunk weights leave a rope spike + a pouch floating at the rest hands (renders/wren/wren_s7fix_walk6_before_after.png);
  v6.1 hides it by gluing them to the forearm / thumb_02_l (the pouch rides the hand to the chest in the run). Next: s3
  profile against the trunk (touches the LOCKED outfit look: the pouch moves onto the hip -- artist call), then retune.
- **Pitchfork DROPPED (2026-10-03, review-log "can we drop the pitchfork from wren", pending commit):** knob HAS_FORK
  (wren_build.py) = False -> no fork mesh / node / material, no 'pitchfork' bone (94 -> 93 bones, 93 -> 92 deform), no grip
  transform or conquest_pitchfork_grip prop, fork gates + report fields removed (seam_fork_mm, min_z_fork, fork_butt_z_range,
  grip_relation_max_dev, fork_shaft_* / fork_to_*), fork renders retired from wren_run.ps1. Tris 48853 -> 47541 (fork = 1312).
  The right ARM keeps the approved carry path (FORK / FORK_* / ROLL search still define the empty hand's frame); only the 15
  right finger rotation channels changed per clip (= the left hand's pose: idle / walk RELAX_CURL + thumb (4, 8), run
  RUN_HAND). The model is RE-CENTRED on the body (feet_origin: SHIFT x -0.0174 -> +0.0144, the whole mesh moves 31.8 mm in
  x in the game frame); every other key equals v6.1 to float32 noise (<= 1.8e-7 quat, <= 1.1e-7 m), not byte-identical; the
  walk's WALK_FOOT_X narrows about the v6.1 frame (s6 SHIFT_V61 / s8 WALK_X0) so the walk legs stay put. --set HAS_FORK=True
  reproduces build digest c9215939245d39b5 exactly. Before/after: renders/wren/wren_nofork_{idle,walk,run}_before_after.png.
  OPEN ARTIST CALL: empty-handed, the carry path reads as a raised open hand at the chest in the walk (and a hand at the
  waist in the idle); a free right-arm swing (mirror of the left) is the natural follow-up but changes the approved arm path.
- **Fists + lower run knee + loop keys (2026-10-04, review-log "Wren in-game verdicts" (1)(2) + the cycle-defect addendum,
  pending commit):** HANDS = a table: HAND_POSES {"relaxed" (v1 idle/walk), "loose_fist" (v1 run), "fist", "grip" (prop grip,
  kept for combat clips)} each {"curl": (knuckle, middle, tip), "thumb": 2-value v1 flex or 5-value (base flex, swing to
  pinky, roll to palm, middle flex, tip flex)}; CLIP_HANDS {clip: (left, right)} = "fist" both hands, all clips (HAS_FORK
  forces right = "grip"). FIST_CURL (80, 100, 35), FIST_THUMB (-60, 27.5, 60, 17.5, 90) = thumb wrapped OUTSIDE across the
  curled index (solved on the rest skeleton, mirror-exact L/R). RUN: RUN_KNEE_DRIVE_DEG = 25 (peak thigh angle below
  horizontal, keyed frames; v1 measured -0.28 = just above horizontal); the drive key's ball height is bisection-solved
  (0.299 m) and its ball moved 0.20 -> 0.08 m ahead (RUN_SWING[1]) so the foot stays cocked under the lower knee (at 0.20
  it skimmed 12 cm off the floor = a jog shuffle). Run gates held: seam 0, slip 1e-6, airborne f5-6/f11-12 (clearance
  11.5 -> 12.8 mm), torso crossings 24 (= the known s7 defect), stride 2.333 m. Blender keys changed ONLY fingers/thumbs
  (all clips) + thigh/calf/foot + the coupled sash ties tie.0/1 (RUN_TIE_LIFT) in the run; the glb shows <= 0.052 deg
  exporter jitter elsewhere (a plain re-export of the unchanged v-prior blend shows the same). LOOP KEYS: the glb samplers
  started at 1/24 s (keys at frames 1..N+1) -> the game held the first pose 42 ms per wrap; export_anim_slide_to_zero=True
  now: idle 97 keys 0..4.0 s, walk 27 keys 0..1.0833 s, run 13 keys 0..0.5 s (the last key = the first: glTF's closing key
  carries the period). New gates: seam_joints.boundary_motion_ok (first / last key interval >= BOUNDARY_STEP_MIN 0.25 x the
  median step) + GLBLOOP (t0 = 0, t_last = N/24, N+1 keys, both boundary intervals moving, closing dev < 0.1 deg).
  KNOWN SIDE EFFECT (needs s3/s7, outside this lane): the pouch is 92 % weighted to thumb_02_l, so the fist's thumb wrap
  moves it 39 mm mean / 59 mm max (run 48 / 73) and turns it ~90 deg (flap sideways) in every clip -- see
  renders/wren/wren_fist_{idle,walk,run}_before_after.png; the queued s3 pouch-onto-the-hip lane fixes it.
- **Key knobs:** HAND_POSES / CLIP_HANDS / FIST_*, RUN_KNEE_DRIVE_DEG, HAS_FORK, HAIR_NORMAL_CARRIER, EYE_SCALE, LIP_* / MOUTH_* (seal, smirk, smooth, proxy), RIBBON_* /
  LAYER_* (hair), HAIR_INTERIOR_R, NEAREST_TIE. All top-of-file in wren_build.py.
- **Unit gates beyond the contract:** face probe (under-eye/mouth numbers), mouth
  probe (second-feature traces), hair diag (interpenetration/kinks), HAIRSTABLE,
  poke-through, hair-into-head. Runner: improve/wren_run.ps1 (~160-200 s battery).
- **Known residuals (accepted or queued):** corner specks at mouth-line ends
  (closeup-only); outer-corner under-eye remnant; paint tiers read blocky on locks;
  33 locks vs the figure's denser count (+45-lock version needs ~2.5k harvest â€”
  queued grown); fringe-on-skin discontinuity vs EYE_SCALE edits (queued grown);
  muzzle side-to-side wrap (open artist question).
- **Open artist questions:** smirk strength/side; under-lip shading absent (confirm);
  hair paint tiers vs one-tone; lock count increase; back-lock length; winter
  angel-ring tint; run: arm pump amplitude (capped by the cloak drape), cloak trail; the empty right arm (keep the carry
  path vs a free swing); the weapon design (HAS_FORK machinery re-usable).
- **Stale items fixed 2026-10-03:** s8 conquest_look string follows HAIR_NORMAL_CARRIER; wren_run.ps1 reads the hair-normal
  bake pair only when this build wrote it; wren_render.py '--hair-normal-flat' retired.

