# New from-scratch unit — build contract (what `<unit>_build.py` must provide)

The scaffolded runner / render / clips / compose (new_unit_scaffold.py) work unchanged for any
unit whose build honours this page. The REFERENCE IMPLEMENTATION is `firefly_build.py` +
`firefly_parts.py` (commit f77dff6): read them first and copy their structure, helpers and idioms
(sha, mesh_arrays, keep_islands, iso_cut, make_mat, repaint, geometry_digest, glb_carries, the
clip keying loop). Shared kits, read-only: `magmoo_sdf.py` (SDF grid + primitives + marching
tets), `palettes.py`, `rigkit.py` (vine_weights / vine_wave / assign_action), `export_glb.py`.

## Files the lane writes
- `improve/<unit>_build.py` — one headless run; argv flags `--preview <blend>`, `--digest-only <json>`,
  `--set NAME=value` exactly as firefly.
- `improve/<unit>_parts.py` — pure-numpy closed parametric parts (Part class, lathe, torus, ...;
  copy what is needed from firefly_parts.py, add the unit's own generators).
- `palettes/<unit>/default.json` — every region; base tones PIXEL-SAMPLED from the sheet
  (intake.json `swatches` + direct samples of the drawn surfaces), never eyeballed. Optional
  extra skins `palettes/<unit>/<skin>.json` (`"extends": "default"`).

## Build structure (firefly order)
1. TUNABLE CONSTANTS block near the top, artist-facing name in each comment, in SHEET UNITS
   (1 unit = 100 px; z = (floor_y - y)/100, x = (x - axis_x)/100 from intake.json — cite the
   view and px for every measured constant).
2. Organic body = ONE SDF (smooth unions) -> polygonise (HIGH, the bake source) -> collapse
   decimation to the LOW shell (deterministic).
3. Colour regions on the shell by part ownership / fields, every boundary iso-cut into the mesh.
4. Hard-surface / sheet pieces as closed parametric parts (0 open edges unless a single-surface
   sheet with a double-sided material is intended — report every open part with its reason).
5. Palette -> Col / Glow corner colours (+ Col alpha if translucent regions), flat shaded,
   Smart UV, bake normal + AO from HIGH (+ closed parts) at seed 0.
6. Rig: `root` (non-deform, at origin) > deform bones; <= 4 influences, weights sum 1.
7. Clips named from rigkit.ALLOWED_CLIP_NAMES (idle + walk minimum for clip_names), integer
   harmonics per loop (frame N+1 == frame 1), every deform bone keyed in every clip, LINEAR,
   use_cyclic. A clip whose movement intent the artist has not given ships as PLACEHOLDER and
   says so in `conquest_clip_status` + the report.
8. glb export via `export_glb.add_glow_attr` then `export_scene.gltf` (firefly's settings);
   one rigged `<unit>__<skin>.blend` per extra palette + the repaint proof.

## Outputs + names the scaffold reads (do not rename)
- `improved/<unit>.blend` + `improved/<unit>.json` (report), `improved/textures/<unit>_{normal,ao}.png`
- `rigged/<unit>.blend`, `rigged/<unit>.json` (with `digest: {parts: {...}, combined}` and the
  parts `bake_normal`, `bake_ao` among them), `rigged/<unit>.glb`, `rigged/<unit>__<skin>.blend`
- `--digest-only <json>` writes `{"digest": {...same shape...}, "tris": N, "seconds": s}` and saves nothing else
- bake arrays to `%TEMP%/<unit>_{normal,ao}_{main,twin}.npy` (twin when --digest-only)
- rest pose: min z = 0 (the floor), bbox centred on X/Y, transforms applied, front = -Y
- main mesh custom props (the contract checker reads them): conquest_unit, conquest_character_id,
  conquest_tier, conquest_tri_budget, conquest_max_height, conquest_max_footprint,
  conquest_yaw_fix_deg, conquest_front_anchor, conquest_front_landmark, conquest_facing_rule,
  conquest_source, conquest_scale_policy, conquest_clips, conquest_clip_status
- `conquest_focus` (JSON on the main mesh): `{"<closeup>": [lo_xyz, hi_xyz], ...,
  "views": {"<closeup>": [angle_deg, elev_deg]}, "axis_xy": [x, y]}` — every close-up the
  render's `focus` view should produce (2-4 of the sheet's detail panels), world coords after
  the centring shift; `axis_xy` = the sheet's character axis in world X/Y (the ortho cameras
  centre on it).

## Gates the lane must quote (from the runner's output)
DIGEST line (identical=True, or except-bakes with gate PASS); both contract checkers' `N checks,
M failed` (cell_fit is report-only: natural sheet-unit scale; clip_names fails only if a clip
is genuinely missing); tris vs the declared tier window; open-edge table; the
sheet_vs_model overlay looked at, with the lane's own read of where the silhouette departs.

## Known kit hazards (workarounds live in the BUILD unless the kit says it handles one)
- **SDF grid values at exactly 0** (magmoo_sdf.polygonise): HANDLED IN THE KIT since 2026-10-09. polygonise pushes
  |F| < NODE_EPS * step (NODE_EPS = 0.005, derivation in magmoo_sdf.py) off zero on a copy of the grid before
  extraction, so crossings never weld into coincident vertices / zero-area slivers (the cause of Blender's collapse
  decimation folding a large high toward a hull: shadowbun 2026-10-09 low bbox 1.36 x 4.45 vs the high's 1.80 x 5.01;
  firesprite body raw: low bbox 0.71 of the high, p99 0.61). New builds need no NODE_EPS / CORNER_CLAMP of their own;
  still gate the low's bbox + high->low p99 against the high. If a new grid's measured slope L / step exceeds 526 /m
  (the kit's derivation bound; worst committed grid 412 /m), re-derive NODE_EPS rather than adding a build-side clamp.
- **SDF band vs smooth-union k**: the grid BAND must exceed the largest smooth-union k used (seedant: band 0.08
  under an arm blend left seam lines on the face; firesprite: a staircase hip). Pick BAND >= max k + 2 steps.
- **Fur / tuft language**: tufts read SOFT only when blunt, short, varied and smooth-unioned deep; rows of sharp
  downward cones read as pinecones / scales (shadowbun v1 gate). Clouds = overlapping rounded bulges.
- **One run per unit at a time**: runners write bake / determinism scratch to fixed %TEMP% names
  (<unit>_normal_main.npy, magmoo_det, ...). Two simultaneous runs of the SAME unit (e.g. a worktree lane and the
  main checkout) overwrite each other's twin evidence. Different units in parallel are fine.
