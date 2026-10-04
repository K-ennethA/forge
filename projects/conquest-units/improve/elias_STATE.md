# Elias - unit state (orchestrator-maintained; briefs point here instead of restating history)

- **Current:** v7 DEFECT FIX (review-log 2026-10-04 "Elias staff/robe": "not holding his staff ... his robe stretches") on the v6
  beard / v5 scalp (both byte-identical: SCALPDIGEST 9c9d60d59a0df692, BEARDDIGEST 7a8c8264831b975f). Runner elias_run.ps1 [-Twin]
  ($VER elias_v7, ELIAS_BASE elias_v6; render economy: full body + props + hold_* views; strip renders/elias/elias_v7_hold_compare.png
  = v6 | v7 | SHEET for front / 3/4 / hold_tq / hold_side). v7 = (1) s3: belt / pouch / scroll hosts are TRUNK_F only (the belt band's
  side rays hit the hanging hands -> two spikes to the wrists; the mantle, hung over the belt, tented out over both hands = the
  "stretch"); (2) s7 TORSO_HUNG port (robeskirt, belt, buckle, pouches, scrolls, satchel + strap; the coat too) = transfer_trunk, arm
  chain incl. clavicle never a source; (3) s7 STAFF HOLD BAKED INTO THE BIND POSE: staff planted upright (STAFF_HOLD out -0.150 / fwd
  -0.120 vs the right shoulder joint, grip_at 0.70 = 1.302 m), two-bone IK + roll search (roll 240, wrist 23.7 deg after
  HOLD_TWIST_SHARE 0.5 of a 35.9 deg twist), fingers HOLD_CURL (Wren grip), thumb "wrap" solved (-30, 30, -30, 60, 48); meshes LBS'd
  into the pose, 19 bones re-seated, model re-centred (+38.3 mm x); (4) s6: forearm / hand faces never in the under-coat harvest.
- **v7.1 HAND DENSITY (a1b0857 + this):** s6 HAND_DECIMATE {L 0.40, R 0.60} (Varden glove pattern): per hand the interior verts
  (all incident faces hand-dominant + one region; wrist line / region borders exact) collapse-decimated, bake high keeps the full
  hands, weights re-taken by transfer(). Hands 3,167 -> L 1,265 / R 1,899 tris. Grip numbers unchanged (bone-derived); mesh
  fingertip pads 8.6-12.0 mm from the shaft axis (= v7, inside the ~14.5 mm shaft).
- **Numbers (v7.1):** 48,519 tris (budget 50k: 1,481 left). glb 650059ead1a68a16, 10,526,796 B; glb + textures byte-equal across 3
  builds (the float bake_normal digest jittered once: sub-8-bit, the known bake jitter). Checks: improved 6/7 (cell_fit report-
  only), rigged 8/11 (+ clip_names / clip_loops by design) = the v6 exception set.
- **LOCKED:** v5 scalp (HAIR_MASSES, palette hair family; SCALPDIGEST 9c9d60d59a0df692 must stay).
- **v6 beard:** STRANDS OVER A THIN CORE. Core shell (BEARD_CORE: v4 column builder, 6 deg columns, 2.5-4.8 mm thick, hem 26 mm below
  the chin, rounded U, painted dark-inner below the chin) keeps the zone covered; 7 masses / 28 ribbon locks on top (BEARD_MASSES:
  cheeks beardsL/R rigid, jaws beardL/R -> beard.L/R, chin beardC -> beard.C; MUSTACHE_MASSES mustL/R rigid, two sweeps, root_k 0.70),
  widths 5..40 mm (spread >= 3.1 per mass, 8.0 overall). Beard-only ribbon knobs (BEARD_RIBBON taper / belly / tip / sway 0.06 /
  horizontal-radial frames RIBBON_RADIAL_AXIS) swapped in only while the beard builds; own layer resolve (masses bottom -> top as listed),
  skin/core push (core window -3 mm), tuck OFF (BEARD_TUCK: horizontal bars). Built AFTER the scalp (measured: scalp independent of it).
  BEARD_ZONE: neck_drop 40 -> 20 mm, neck_rise (34 mm, |x| 18 -> 50 mm), cheek point (0.080, -0.012) removes the 28 mm top-edge step
  at |x| 72 mm (the v4/v5 deep-visible sliver). BEARD_CHAINS = s5's mean lock paths (s7 override identical).
- **Numbers (v6):** 49,749 tris (budget 50k: 251 left); beard 3,832 (core 816 + beard locks 2,442 + mustache 574; v5 3,432); body
  15,593 (zone cut). Beard extent: lowest 37.6 mm below the chin. Zone probe (21 views): 212 visible / 899 mm2, deeper than 3 mm 28 /
  184 mm2 (v5 168 / 1,472 and 35 / 562). Beard layer resolve: top-sheet pairs 26 -> 17. Mustache lip clear 2.14 mm. Carry p90 0.0046
  deg. Cap exposure 23.5 % (= v5). Scalp <-> glasses 0 pairs; scalp <-> beard 0 pairs (> 20 mm). Twins byte-equal (glb 47210ace2ee7164b,
  10,813,316 B, combined digest dff7486be071658b).
- **Unit gates beyond the contract:** SCALPDIGEST (= v5), BEARDCOVER (zone probe vs v5), HAIRCLEAR, CAPEXPOSE, MASSES + BEARDMASSES spread, tris <= 50k.
- **Known residuals:** scalp (v5): paint tiers read as per-segment patches on wide locks; crown flick knob at the whorl; cap at both
  temples; sidel.1 tip pushed 5.5 mm. Beard (v6): zone still visible <= 5 mm deep under the nose at grazing side views (between the
  mustache roots' top edge and the skin) + the inherited sideburn sliver by his right ear (psi -80..-84); jaw S / cheek locks read as
  thin edge-on slivers at the face outline; chains beard.L / .R carry 2 locks each (the other jaw locks only dip their tip below the
  leave line: rigid); beard tone sampled, close to the skin luminance. Rigged check: clip_names / clip_loops fail (no clips, by
  design); cell_fit report-only (staff).
- **Open (v7):** capelet stays shoulder-hung
  (704 verts arm-weighted, rides the raised upper arm). 
- **Open artist questions:** the v6 beard read vs the sheet (clump size / count, point length 38 mm, cheek coverage height, mustache
  sweep density); beard tone vs skin; glasses lens + frame colour; staff scale / hold height (v7 grip 1.30 m; the sheet's fist sits near the shoulder); tome rest place; age lines; movement intent
  before clips (beard sway).
