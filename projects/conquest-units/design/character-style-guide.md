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
  normal bake (egg from the hair hull) leaned ~45% toward per-lock normals.
- Flow from a part/whorl per the sheet; follow-through chains on fringe/sides/tail.

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
