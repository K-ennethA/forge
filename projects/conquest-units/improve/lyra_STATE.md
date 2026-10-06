# Lyra - unit state (orchestrator-maintained; briefs point here instead of restating history)

- **Current:** v1.1 (artist-verdict fixes on the v1 draft + hair resume; review-log 2026-10-06 "Lyra v1 verdicts", 3
  markups in design/reference/lyra-v1-*-annotation.*). Sheet design/reference/lyra/lyra_sheet.webp (1536x1024). Runner
  improve/lyra_run.ps1 [-Ver lyra_v11 -FixOf lyra_v1] (-FixOf = render economy: front + head views + fix strip only).
  No clips (L5 movement intent unanswered), no determinism twin yet (draft mode).
- **v1.1 fixes:** (1) NECK PATCH root cause = the authored jaw-shadow (skin_shadow) edge: per-vertex yes/no ray test +
  head/neck bone-ownership border projected a ~5 mm sawtooth onto the neck (proved by AO/normal-cut differentials +
  magenta region override). Fix in s2: shadow edge is a drawn Gaussian-smoothed per-angle curve, knob JAW_EDGE (4,10) deg;
  74/74 boundary edges on the curve. (2) HAIRLINE: HAIRLINE_SIDE now (0.012, -0.004, -0.004) arch/sideburn/behind +
  HAIRLINE_EAR_CE (-0.30, 0.13); bare skin above ear 26 -> 9.8 mm over eye centres; CROWNSKIN 0 faces (784 scalp faces).
  Part/fringe/tail tables untouched. Side effects: cap exposure 40.7 -> 47.0%, crossings 189 -> 186. (3) CHIN: MPFB dials
  only (chin-height-decr 1.0 NEW, chin-width-decr 1.0, chin-bones-decr 1.0, chin-triangle 0.70); mouth->chin 60.2 ->
  53.5 mm; mouth re-lawed v_ratio 0.289 (mouth-trans-up 1.0). Jaw still ~3-5 mm/side wider at mid-height than the markup
  (geometric squeeze tried + removed: blurred jaw into neck; s1 byte-identical to v1).
- **Numbers (v1.1):** 48,226 tris (budget 50k; main 46,450 + books 1,776; body 13,812 + scalp 5,154). glb
  ca40d57f91944256, 11,227,520 B, 70 joints, _GLOW asserted ALL ZERO (L3). Checks: improved 7/7, rigged 9/11
  (clip_names/clip_loops by design). cell_fit 1.751 m.
- **Face (v1.1):** 6.10 heads (head 274.7 mm) - PAST the house 5.5-6 after the chin cut, head-height dial already maxed;
  OPEN DECISION: chin length vs heads-tall. v_ratio 0.289, mouth 0.671 x eye spacing (47 mm), iris 63.5%, under-eye
  crease 0.30/0.26, flatness 0.40/0.41, mouth relief 0.574 mm. BROW_W 7.2/2.8 mm = inner 0.17 x eye width, DELIBERATELY
  under the house ~0.27 (girl's brow; one knob to raise). Residual: face still reads a bit boyish/older than 17.
- **Hair (post-resume):** 6 masses / 41 locks + 12-lock ponytail (76 mm widest). Soft off-centre part (HAIR_PART ~12 deg
  her right), 8 swept front locks, 3 fringe strands + 2 forehead wisps, SCALP_HUG petals, tie at 58 deg (TIE_DIR), cap
  4 mm + 45 mm feather, HAIR_CAP_SINK 1.5 mm INTO skin (fixed 133-face crown skin-through -> CROWNSKIN probe 0 faces /
  0.0 mm2, 37 views). Tail: TAIL_ROOT_K 0.75, TAIL_THICK x2.6, TAIL_RADIAL 0.85, TAIL_WAVE 20 mm x1.6, tip z 1.09 m.
  Blue ribbon x2 + gold x2, bow, hairpiece + tassel; follow-through chains hair_tail x4, hair_side.L/R / ribbon / necktie
  x3 (spine_03). Vertex-carried proxy normals, 2 groups (scalp+tie / tail).
- **Hair residuals (v2 agenda):** volume/messiness still under the sheet (sides + top, tail fullness from the back);
  lock-lock crossings REGRESSED 98->189 pairs (11,203 tri pairs; top sheets 176/3,132) = the style guide's #1 chopped-hair
  cause - a v2 layer-resolve pass is the known fix; facet kinks p50 14.3 / p90 40.5 deg; exposed dark cap 40.7% (incl.
  part line); angel-ring paint prints as tan bands across wide locks (same as Elias v5 note).
- **Book hold (baked into bind pose, clipless law):** RIGHT arm (L1: sheet front shows right, side shows left - flagged).
  2-3 book stack own bone, HAS_BOOKS knob. Wrist 25.0 deg, elbow 108.9, pads 0.5-4.4 mm, penetration 0.0; clearances palm
  2.81 / forearm 8.16 / chest 3.0 mm; BOOK_CAPELET_CLEAR lays 57 capelet verts behind the stack (weights unchanged).
- **Garments:** TRUNK_HUNG belt/skirt/satchel/scroll (census arm_verts 0); capelet shoulder-hung. Palette 63 regions all
  sampled/derived-with-rule from the sheet; 8 chips recorded.
- **Open artist questions:** L1-L5 in design/OPEN-QUESTIONS.md (book arm + empty-handed option, height 1.70 m, zero glow,
  ribbon dynamics, movement intent) + hair volume/messiness verdict + the boyish face read (brow knob first candidate).
