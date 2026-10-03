# Elias - unit state (orchestrator-maintained; briefs point here instead of restating history)

- **Current:** v6 DRAFT (sheet-matched BEARD, review-log 2026-10-03 "Elias v5 hair APPROVED; beard same treatment") on the v5 scalp
  (APPROVED, byte-identical). Static, no clips. Judge = design/reference/elias/elias_sheet.webp (front / side / back + head panel).
  Runner: improve/elias_run.ps1 [-Twin] (~85 s; $VER elias_v6, ELIAS_BASE elias_v5; glb always; checker on improved AND rigged; -Twin =
  scratch twin build, glb + textures + combined digest compared) -> renders/elias/elias_v6_sheet.png + elias_v6_hair_compare.png =
  v5 | v6 | SHEET for hb_front / hb_tql (3/4 his left) / hb_side (his left) / hb_low (chin-up) / hb_tq (head panel, his right) + one-tone
  column; hb_* = the head + beard focus box (elias_render.py; headc_* kept).
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
- **Open artist questions:** the v6 beard read vs the sheet (clump size / count, point length 38 mm, cheek coverage height, mustache
  sweep density); beard tone vs skin; glasses lens + frame colour; staff scale / hold; tome rest place; age lines; movement intent
  before clips (beard sway).
