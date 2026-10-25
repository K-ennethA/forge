# iron_scale_knight STATE (v2b, 2026-10-17)

- Version: v2b rebuilds on the REGENERATED symmetric Pixal3D proxy (seed 56, symmetric front / back / left / right views, sha e5092e7d; the v1 3/4 proxy is in proxy_v1_3q/, unused). LIGHT finish, config improve/iron_scale_knight_finish.json. DIGEST 5b9953203eaaf6ac (twin identical).
- Frame: fit_side_length Y x0.9317 (+7.3 %). No refloor / rescale (floor = the scale-skirt hem tips at x +-2.1..2.7, feet in the z <= 0.1 slab; height 6.815 u vs the sheet front 6.82).
- Symmetry: raw proxy mirror p99 about x 0 = head 0.017 u / body 0.029 u, so there is NO repose. Final low (18000 tris): build mirror p99 0.063 u (the decimation is not mirror-exact); glb mirror test p99 0.152 u.
- Tone (texture only, light.recolour3d, the v1 idiom re-fitted to the new texture): gold b* >= 14.5 (8.9 % visible = the sheet's 8.9 %) -> 169,127,99; then six rank-matched L* bands (proxy L* 0/10.9/20.9/33.6/40.2/46.6/101 -> the sheet's same-quantile medians x1.05: 39,28,25 / 58,45,42 / 89,70,65 / 133,112,108 / 156,133,126 / 202,175,163).
- Amber eye slits: one design on the -x slit, mirrored to +x. The proxy's own dark slit under the brow plate runs (-0.07,-1.53,5.83) -> (-0.25,-1.17,6.01).
  - rim: the slit's dark texels -> 43,34,28;
  - amber almond: the measured slit texels inside an almond -> 218,157,106 (sheet head-panel amber);
  - pupil line: 52,34,24;
  - painted after the tone bands; dense boxes hold both slits.
- Rig: the spine is on x 0. The arm and leg chains are mirrored. The tail is a 4-bone chain centred on x 0 down the back scale train, (0,0.5,2.6) -> (0,1.75,0.25).
- Gates: compose GATE mean_iou 0.831 (back 0.782, side 0.880); FINISH_GATE finished 0.795 vs proxy 0.785; 0 open edges; checks 7/1 and 11/1 failed, both cell_fit (report-only).
- Open for the artist: the eye slits read small at the 1024 bake. The gold reads slightly peach and blotchy on some plates. The front is wider than the sheet's 3/4 front (5.73 vs 4.26 u: the scale skirt). Movement and height are still open (clips PLACEHOLDER).
- Close-ups: renders/iron_scale_knight/iron_scale_knight_v2b_head_{front,side_px,side_mx,q_mx,q_px}.png, iron_scale_knight_{front,side,back,threequarter}.png, iron_scale_knight_finish_panel.png.

---- v1 (superseded) ----
# iron_scale_knight STATE (v1 draft, 2026-10-16)

- Version: v1 is the raw Pixal3D proxy (front + back + side generation, seed 56, sha 8e555f73) on the LIGHT finish (proxy_finish.py; config improve/iron_scale_knight_finish.json). The geometry is kept as generated: no symmetrise, repose, cuts or parts, and no glow.
- Spec: design/review-log.md "2026-10-16 — NEW UNITS x14", section IRON SCALE KNIGHT. Sheet: design/reference/iron_scale_knight-character-sheet.png. Intake: design/reference/iron_scale_knight/intake.json.
- Frame: `fit_side_length` scales Y by x0.9453 (the proxy side length / front height was 0.634 vs the sheet's 0.600, +5.8 %).
  - No refloor: the floor (z 0) is the -x sabaton's toe tip, a foot contact. The tail's rear-most point sits at z 0.31.
  - No rescale: height 6.805 u vs the sheet front 6.82 / side 6.83.
- What the finish changed (texture only, light.recolour3d):
  - gold trim: texels at b* >= 16 (8.5 % of the visible texels; the sheet has 8.4 %) are re-toned to the sheet gold 169,127,99, with the shading kept and the highlight clip at 1.4. The eye box is excluded from this pass.
  - steel / cloth: a quantile tone map in six L* bands, brightest first. Each band goes to the sheet's same-quantile median colour, so the dark gunmetal reads pale warm steel and the cloth underlayer stays dark.
  - result: the visible basecolor L* p10/50/90 is front 11.8/39.9/74.2 (sheet 13.7/39.0/71.3), side 10.0/41.6/72.8 (sheet 14.4/43.5/68.7) and back 9.9/35.4/65.4 (sheet 15.0/41.1/72.1).
  - bake: ao_from_low, ao_distance 0.2, ao_fill_below 0.04, bake_drop_interior 0.08, uv_island_margin 0.001.
  - dense boxes: head + crest arc (vg 0.3), eye slit (vg 0), tabard (vg 0.3). They hold 30 % of the 18000 tris.
- Left as generated: the helmet, the snout spike (black on the proxy; the tone map lifts it only to a very dark brown), the amber eye slit, the crest arc and the tabard.
- LOCKED: nothing yet (awaiting the artist).
- Knobs that matter:
  - recolour3d band targets (tone_L52_101 200,173,161 ... tone_L0_8 40,29,26), gold_trim 169,127,99 and its b* >= 16 threshold;
  - body_tris 18000; the dense vg_weight values; ao_distance 0.2;
  - clips: idle 72 f, breathe 1.2; walk 32 f, leg_swing 8.
- Open for the artist:
  - movement and height. The clips are PLACEHOLDER. The kit walk dips the feet 0.204 u below the floor; idle dips 0.010 u.
  - the proxy copies the sheet's 3/4 pose: the head is turned to -x, the legs are staggered 1.3 u in y and the tail sweeps to +x. Mirror p99 is 1.01 u, left as generated. The +x foot sits 0.086 u above the floor. The rig's leg and arm chains are the mean of both sides.
  - the snout spike reads dark (black on the proxy), while the sheet draws it pale steel.
  - the tabard's gold fleur emblem and the back's gold diamond are not in the proxy texture. The tabard has only faint gold border lines, so neither emblem is built.
  - the back view scores IoU 0.719, the same as the raw proxy. The sheet's back is drawn 3/4.
- Unit gates: digest twins, bake gate, contract checks (cell_fit is report-only), compose GATE lines, proxy_finish.py gate + panel (renders/iron_scale_knight/iron_scale_knight_finish_panel.png), and the close-ups renders/iron_scale_knight/iron_scale_knight_head.png + iron_scale_knight_tabard.png.
