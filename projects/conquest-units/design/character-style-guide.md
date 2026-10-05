# Conquest character style guide — THE HOUSE STYLE (artist-approved on Wren, 2026-10-02)

The Wren pipeline (improve/wren_*.py at commit d1a43af) is the REFERENCE IMPLEMENTATION.
Every new or reworked humanoid/hero character applies this stack FROM THE FIRST PASS.
Creature units reuse the shared principles (palette regions, glow tiers, determinism,
probes) per their own body plans. Style bar references: design/reference/fe-style/
(archer figure = hair + mouth quality bar; Ashe portrait = face ratios) and
design/reference/anime-3d/ (Alicia Solid = construction: features are paint on simple
smooth geometry).

## Face
- MPFB2 base tuned STYLIZED from the outset: anime dials (eye scale, soft jaw, sharper
  chin, small nose), age/proportion macros per the character. 5.5-6 heads tall reads right.
- SKIN: warm tone SAMPLED from the character's sheet/reference. Never neutral grey.
- FACE ZONE smooth-read: higher texel density + normal map baked against its own flat
  faces (FACE_NORMAL_REF="flat") — renders smooth, flat_shaded contract untouched.
- Authored shadow-shape regions (jaw, fringe) — drawn, never AO-derived. AO floors per
  region class so nothing bakes to black grime.

## Eyes
- Geometric socket+eyeball scale blended over the orbit, falloff FOLLOWING the orbit
  shape (skin outside slides along the original surface — zero nose/temple leak).
- Iris ~55-65% of the visible opening (big but with real white); pupil kept; round
  highlight dot (extra eyeball rings so it stays round); thick tapered upper-lash band
  with a wing; thin lower liner (~0.3 mm); bold brows (inner ~0.27 x eye width).
- UNDER-EYE IS FLAT: straight tangent runs lash-line -> cheek, no bags, no lid line,
  AO floored. Any softness is a painted tint, not geometry.

## Mouth
- ONE unified faint line that IS the mouth, sitting ON the sealed seam: level seam,
  smirk built into the seam geometry if used, line covering seam + 1 mm, gap-free.
- Position/width at the Ashe ratios: ~0.29 of nose->chin below the nose; width
  ~0.65-0.73 of eye spacing (artist settled Wren at 44 mm / 0.725).
- Subtle lip volumes ON the smoothed skin: lower ~1.2 mm soft bump, upper ~0.4 mm
  plane, composed analytically into the mouth normal proxy. Lip zone tinted slightly
  PALER than skin (never lipstick unless the sheet says so).
- Mouth zone skin Gaussian-smoothed; hidden lip rims folded/removed from mesh + bake.

## Hair
- RIBBON LOCKS: smoothed spines (13-19 sections, crowded into bends), flip-proof
  section frames, bend limit, 6-vertex sections tapering to sharp points, narrower
  feathering tiers. NO per-point clearance projection (use the smooth push).
- LAYER RESOLVE: one merged non-looping order for overlapping locks, smoothed
  lift/tuck rounds (roots exempt). Interpenetration is the #1 cause of "chopped" hair.
- Paint by WHOLE SEGMENTS: angel-ring as a straight stroke; layer shadows as TUCK
  SHADE (covered segments take the crevice tone) — never vertex-level cut bands.
- Dark inner cap; feathered hairline (thick caps read as helmets); hair on its OWN UV
  strip (overlapping locks otherwise bake blotches onto each other); smooth-proxy
  normals (egg from the hair hull) leaned ~45% toward per-lock normals, carried as
  CUSTOM SPLIT VERTEX NORMALS (HAIR_NORMAL_CARRIER="vertex", proven on Wren
  2026-10-03: decode p90 40.75deg->0.006deg, facets gone; the tangent-space map
  carrier is deprecated — it cannot represent ~27% of proxy normals). Hair faces
  are the ONLY flat_shaded exemption (checker enforces hair-prefixed regions).
- Flow from a part/whorl per the sheet; follow-through chains on fringe/sides/tail.
- MASS-FIRST HIERARCHY (law since Elias v5 / Varden v5, both approved-path): the scalp
  is 4-7 PRIMARY MASSES silhouette-matched to the saved sheet's views (front/side/back
  + head panel), each mass composed of ribbon locks with width spread >= 3:1 inside the
  mass. Flow follows the style's natural origin (a part, the hairline for swept-back,
  a whorl ONLY where the sheet shows one) — never uniform whorl-radiated ribbons.
  SILHOUETTE IS JUDGED AGAINST THE SHEET IMAGE, never a text transcription (the Elias
  bowl-hair failure); every sheet is saved under design/reference/<unit>/ at
  transcription time and tones are PIXEL-SAMPLED from it, never eyeballed.

## Beards (law since Varden v4 / Elias v6)
- A beard is a CONFORMING VOLUME, never hanging strips over a painted zone.
  SHORT CROP: one shell, thickness ramp, serrated jittered hem, painted multi-step
  stubble fade into the skin (paint the fade, never cut it - geometry fades cost
  ~1k tris/step). LONG: clumped strands over a thin core shell (core covers the zone
  by construction and reads as dark inner beard), lobed rounded taper per the sheet,
  mass-first widths like the scalp. Mustache joins as soft sweeps - no teeth/crevice
  paint at mustache scale (reads as piano keys). The painted under-beard zone is NEVER
  the visible surface (root width 0.8, edge-lift onto skin). Crop beards ride rigid;
  long beards keep follow-through chains anchored to the hanging mass.

## Rig + clips (laws from the Wren locomotion waves, 2026-10-04)
- STANCE/IK: knee poles aim over each foot's own toes (never the turned rest-knee
  direction); toe-out sign VERIFIED by render (a flipped sign knock-knees every clip);
  ankle spacing tracks the hip half-span per clip (LEG_TRACK), not the rest A-stance.
  Hip-knee-ankle must track vertical in front view (~<2deg) in idle AND locomotion.
- HANDS: a HAND_POSES table (relaxed/loose_fist/fist/grip; 5-param thumb) + per-clip
  per-hand CLIP_HANDS assignment. Out-of-combat clips are EMPTY-HANDED closed fists
  (artist law); "grip" exists for combat clips only. Props stay own-bone with the
  grip machinery behind a HAS_<PROP> knob so removal/re-add is one switch.
- CLIPLESS UNITS: the BIND POSE is what the game shows - bake the prop hold (grip
  applied, IK-solved arm, thumb wrap) into the bind pose, never leave the recorded
  grip "evaluated only".
- LOOP KEYS: clips export slide-to-zero (keys 0..N/24; a leading duplicate key = a
  static hold every wrap). Gates boundary_motion_ok + GLBLOOP are mandatory — a
  first-vs-last seam check alone misses doubled endpoints. Locomotion arm swings are
  double-arm, opposite phase; pump amplitude is capped by the measured cloak-crossing
  ceiling, not guessed.
- GARMENT WEIGHTS: trunk-hung garments (sash, belt, pouches, straps, mantle, robe
  skirt) NEVER source weights from arm-chain bones (TORSO_HUNG pattern) — and their
  GEOMETRY is built against trunk-only BVHs: a whole-body scan catches the resting
  hands and builds garments out to them (the Wren pouch / Elias belt disease), then
  hides it by gluing to arm bones. Shoulder-hung (capelet) stays arm-coupled.
- Hidden-geometry harvests must exclude forearm/hand faces (enclosed fingers read as
  "hidden" and get deleted). Hand interiors are decimation budget (keep region
  borders + wrist line; bake from full density).

## Pipeline invariants (all characters)
- Tie-invariant nearest() for every BVH lookup that must be stable (NEAREST_TIE);
  query uncut surfaces. HAIRSTABLE-style decoupling gates where subsystems must not
  couple (face edits never move hair; prove it in the runner at zero wall cost).
- Interior faces excluded from region ownership (HAIR_INTERIOR_R pattern).
- Probes as gates: face probe, mouth probe (differential renders), hair diag
  (interpenetration/kink metrics). Measured numbers in every report.
- Weapons/props on their own bone + node, grip transform recorded, roll auto-picked
  for least wrist bend.
- Determinism twins + the bake tolerance pattern; per-unit STATE.md; render economy.
