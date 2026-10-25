# raven_shade STATE (v3, 2026-10-17)

- Version: v3 = the REGENERATED Pixal3D proxy (symmetric views: front = the sheet's back silhouette with its image-left half mirrored, side + mirrored side; seed 56, sha 92e56d17) on the LIGHT finish, with paint only: two red eyes, two blood-red last feathers, a tan diamond pendant, and the v1/v2 steel-blue / charcoal tone. There is no turn and no repose. Config: improve/raven_shade_finish.json. DIGEST 914c2a4aa6c671a7: the twin is identical and the bake diff is 0 texels.
- Spec: design/review-log.md "2026-10-17 — Raven Shade v2 face note" (artist: "the face looks crooked"). The lane brief's V3 section wins over the head-graft reading in that entry: the whole proxy was regenerated instead. The v2 asks carry over ("should have 2 eyes"; "make its last wing feather red so it looks like a blood soaked blade"). Sheet: design/reference/raven_shade-character-sheet.png. The old 3/4 proxy is in proxy_v1_3q/ and is not used.
- v3 changes:
  - BASE: the new proxy is symmetric as generated. Mirror p99 is 0.041 u for the whole body and 0.029 u for the head (z > 4.3) about x = 0; the best head plane is x = 0.00 (+-0.02 gives 0.045 / 0.050). So v2's `light.yaw` and `repose` are dropped, and the rig is re-fitted on x 0.
  - FRAME: side-length fit Y x0.9355 (+6.9 %). No refloor and no rescale: the lowest vertex is a -x talon tip (z 0.000), and the height of 6.234 u matches the sheet front height of 6.24 within 0.1 %.
  - DENSE: head, both eyes, the pendant and, new in v3, both feet. In run 1 the decimation shortened the +x talon tips asymmetrically: the +x foot sat at z 0.089 vs 0.000 for -x. The feet box brings it to 0.059 (the proxy itself is at 0.035).
  - EYES (paint): one crisp eye design, drawn on a z-buffered render looking straight into the -x eye and used unchanged on the +x eye (the mesh is mirrored into the same camera), so the pair is level and matched. The outline is the union of both generated pale almonds, smoothed. The layers are:
    - rim: dark surround 36,33,40, 0.014 u band;
    - sclera: 201,190,189 (sheet face panel);
    - iris: ring 141,55,65, r 0.055 u, forward of centre and clipped by the upper lid as on the sheet;
    - core: 173,53,68;
    - pupil: slit 58,20,28.

    Spheres tile each layer's visible texels at >= 98 % purity.
  - BLOOD FEATHERS (paint): on each side the last blade is the outermost, lowest-hanging blade of the folded wing, at the back-outer corner of the cloak (tips (1.456, 1.726, 0.702) / (-1.445, 1.730, 0.697)). The blade's texels are those within 0.006 u of the faces up to 1.25 u geodesic from the tip. Bands from the tip:
    - edge 0..0.08 u: 90,25,32;
    - 0.08..0.40 u: 141,55,65, full strength;
    - 0.40..0.65 u: strength 0.75;
    - 0.65..0.90 u: strength 0.45;
    - 0.90..1.25 u: strength 0.20.

    The blade's shading is kept and neighbouring blades are untouched.
  - PENDANT (paint, decal): the new proxy has only a small knob (0.12 u) on a brown V-strap, which read as a brown smear. A front-view diamond decal is painted at (0, 4.34), 0.32 x 0.48 u (0.8x the sheet's 0.41 x 0.60 so it clears the beak tip): a 0.05 u tan frame (160,136,124) and a dark stone (70,62,66). There is NO back clasp on the new proxy (the back is feathers only), so none is painted.
  - TONE: the v1/v2 steel-blue tips and charcoal recolours are kept, with the face and pendant exclude boxes re-fitted. Basecolor L* p50 is 14.1 vs the sheet's 16.2.
- Gates: contract checks 7 / 1 failed and 11 / 1 failed, both cell_fit only (report-only). Open edges 0, 18000 tris. Albedo coverage 92.8 %, normal 93.7 %.
  - Sheet gate: front 0.745 (proxy 0.741), side 0.881 (proxy 0.846), mean 0.813, none flagged (v2 was 0.800). GATE bake_reference=none is a known kit gap.
  - Finish gate: mean 0.807 vs the raw proxy 0.792.
  - Mirror p99: 0.059 u build (head 0.049 u on the low), 0.141 u on the glb.
- Evidence (renders/raven_shade/):
  - raven_shade_v3_head_front.png, raven_shade_v3_eyes_front.png;
  - raven_shade_v3_head_side_mx.png, raven_shade_v3_head_side_px.png;
  - raven_shade_v3_pendant.png;
  - raven_shade_v3_feather_red_px.png, raven_shade_v3_feather_red_mx.png;
  - raven_shade_finish_panel.png.

  raven_shade_face.png and raven_shade_v2_*.png are left over from v2 (stale).
- Knobs: the eye design (iris r 0.055 u, forward 0.12 W, lid clip 0.72 r, the colours); the blood bands (0.08 / 0.40 / 0.65 / 0.90 / 1.25 u and the strengths); the pendant diamond (centre z 4.34, half-sizes 0.16 / 0.24, frame 0.05); the feet dense box.
- Open for the artist:
  - movement + height: the clips are PLACEHOLDER.
  - The +x talon stands 0.059 u above the floor (proxy 0.035; the decimation). A leg `plant` repose would close it but was not used (the v3 brief: little or no repose).
  - The pupil slit is texel-limited (about 16 texels per eye) and barely reads at close-up distance.
  - The scar streaks are as generated: pinkish marks on the crown and under both eyes. The sheet draws the scar on one side only; it is not painted or removed.
  - The pendant is a painted decal on generated geometry. It reads from the front; from the side it wraps onto the knob and strap.
- LOCKED: nothing yet (awaiting the artist).
