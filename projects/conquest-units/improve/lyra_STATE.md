# Lyra - unit state (orchestrator-maintained; briefs point here instead of restating history)

- **Current:** v1 DRAFT + hair resume (review-log 2026-10-06 "NEW UNIT: Lyra"; sheet design/reference/lyra/lyra_sheet.webp,
  1536x1024). Runner improve/lyra_run.ps1 (~67 s headless: palette, build, renders, checks, probes, compare strip).
  No clips (L5 movement intent unanswered), no determinism twin yet (draft mode).
- **Numbers:** 48,465 tris (budget 50k; main 46,689 + books 1,776; scalp 5,286 + tail 2,196; HAND_DECIMATE 0.36/0.52 paid
  for the tail). glb ce41333bacaa422b, 11,289,848 B, 70 joints, _GLOW present + asserted ALL ZERO (L3). Checks: improved
  7/7, rigged 9/11 (clip_names/clip_loops by design). flat_shaded: hair exemption only (5,290 faces). cell_fit 1.736 m.
- **Face (full house stack):** 5.91 heads, v_ratio 0.278, mouth 0.671 x eye spacing (47 mm), iris 63.5%. Probe: under-eye
  crease 0.30/0.26 mm, flatness 0.40/0.41. BROW_W 7.2/2.8 mm = inner 0.17 x eye width, DELIBERATELY under the house ~0.27
  (girl's brow; one knob to raise). Residual: face reads boyish/older than 17.
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
