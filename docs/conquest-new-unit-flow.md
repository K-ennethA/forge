# Conquest new-unit flow: smart intake -> sheet proxy -> build -> numeric gate

2026-10-11. This applies to NEW units only. Existing units keep their committed intake, builds and outputs.

The skill `.claude/skills/conquest-new-unit` runs this flow. The build lane follows
`projects/conquest-units/improve/templates/new_unit/BUILD_CONTRACT.md`. Paths below are relative to
`projects/conquest-units/`. Python is the service venv, run with `-P`.

## The house rules (unchanged)

- **The sheet is the spec.** Build only what it draws, and report every deviation.
- **The rest pose is symmetric.** A swung tail or staggered legs on the sheet are built mirror-symmetric.
- **Ask the artist only about movement and in-game height.**

## 1. Smart intake

Command: `improve/sheet_intake.py <sheet> <unit>`

It runs the same measurement as before, twice:
1. Pass 1 uses the caller's flags.
2. Pass 2 fills in automatic flags wherever the caller gave none. A flag you pass always wins over its auto value.

`--no-smart` reproduces the pre-smart script exactly. This was checked on all 30 house sheets: intake.json, the
overlay PNG and stdout were identical for every sheet.

What smart intake does, and what used to be done by hand instead:

| step | replaces |
|---|---|
| Removes the title, rule lines, framed detail panels and unframed text columns | `--region` |
| Splits views fused by touching parts along the weakest contact, using an opening then geodesic growth | `--cuts` / `--assign` |
| Mattes a dark subject on a dark sheet with BiRefNet (when meshgen's ComfyUI is up), then carves back out the background pockets the subject encloses | `--bg-tol 12` |
| Sets the floor at the lowest body contact. It ignores drop shadows, thin curls and pools, a narrow tail tip at the end of the view, an unpaired narrow tail hanging below the feet, and floating parts | `--floor` |
| Sets the axis by mirror symmetry of the eroded body on front and back views, and at the centre of the floor contact span on the side view | `--axis` |
| Flags a 3/4 front or back (mirror IoU below 0.75 AND tone asymmetry of 0.30 or more) and leaves it out of `ortho_views` | `--ortho-views` |
| Detects which way the side view faces (`side_facing`) from colour: face colours appear in the front view but not the back | eyeballing the head |
| Falls back to relaxed gradient chips, then k-means colours of the views, when no palette chips are found | sampling by hand |
| Reports the front-vs-side height disagreement as a % | reading two numbers |

It also writes these, which the lane brief and the gates use:
- `view_masks.png`: the silhouettes the gates score against.
- `auto_flags` / `auto_flags_cli`: the equivalent flags.
- `intake_notes`: lines copied verbatim into the lane brief.

On the 30 existing sheets, with no flags at all, measured against the hand-tuned committed intakes:
- **Views:** 27 of 30 split within 25 px of the hand split.
  - gemwing's back view now keeps its own tail tip, which the hand cut gave to the side view.
  - lava_komodo's side view swallows a detail panel that touches its tail. Pass `--region` for that sheet.
  - pangolin's back view is 33 px wider.
- **Side facing:** 29 of 30 correct. The miss, mystic_snow_spirit, is printed on the overlay so you can catch it.
- **Front/back ortho choice:** matched on 29 of 30. The miss is ember_stump.
- **Floors:** within 5 px of the hand values on 17 sheets. Most of the larger differences are cases where the
  hand value was wrong: eclipse_wolf's paws are 22 px lower than its committed floor, and gemwing's back-view
  floor was on the tail. Some misses remain: petal_cobra's resting tail and pangolin's tail tip.
- **Not detected:** a side view drawn 3/4, which occurs on 5 sheets. Pass `--ortho-views` for those.

## 2. Sheet proxy (when the GPU is free)

Command: `improve/sheet_proxy.py <unit>`

This generates a Pixal3D 3-view mesh of the sheet. The sheet proxy is a 3D REFERENCE ONLY (option ii): the
build stays procedural.

Steps:
1. **Cut the views.** Front, back and side are cut out as RGBA at sheet px. Every view's floor line goes on the
   same row. Anything drawn more than 2% below the floor line is clipped (`--keep-below-floor` keeps it). That
   clip removes perspective tails and curls, which would otherwise stretch the shared frame and make the
   proxy short.
2. **Generate.** meshgen runs Pixal3D multi-view with seed 56 and `options.shared_frame`. The side view goes in as
   meshgen `left` when the head points image-left, and as `right` otherwise. A view the intake flagged 3/4 is
   left out; a clean back, mirrored, stands in for a 3/4 front.
3. **Check VRAM first.** The script needs about 8.6 GB free. If there is less, it exits with code 3 and the unit
   goes ahead without a proxy.
4. **Freeze.** The mesh is saved as `design/reference/<unit>/proxy/<unit>_proxy.glb` and its sha256 is recorded.
5. **Normalise.** The mesh is brought into the sheet frame: Z up, front -Y, floor at 0, and the front height equal
   to the sheet's front height.
6. **Score.** Each view gets an IoU, a row width error and a side length / front height comparison.
7. **Measure.** It records cross-sections at 8 height bands and landmark depths.
8. **Write.** Everything goes into the intake.json `proxy` block, and the panel `proxy/<unit>_proxy_vs_sheet.png`
   is rendered.

The proxy passes ("reliable") when mean IoU is 0.75 or more AND the side length is within 5%. Otherwise the
verdict is "proxy unreliable, work from the sheet".

## 3. Scaffold and build

1. `improve/new_unit_scaffold.py <unit>` also writes `improve/<unit>_lane_brief.md`, pre-filled with the intake
   notes and the proxy verdict.
2. The lane cites `proxy.sections` / `proxy.landmarks` (with the sha) for the depths the sheet cannot give, but
   only when the proxy gate passed.
3. Bake against the FLAT low. This is BUILD_CONTRACT step 5, and the default for every new unit.

## 4. Numeric sheet gate

The scaffolded `<unit>_compose.py` scores each true-ortho view with the same metric the proxy used, and the runner
prints the results. The lines are:
- `GATE view=... iou=... in_frame=... row_err=...% | proxy iou=...`
- `GATE mean_iou=... flagged=...`
- `GATE bake_reference=flat`

It also writes `renders/<unit>/<unit>_sheet_gate.json`.

A view more than 0.05 IoU below the proxy is FLAGGED. The lane fixes it or explains why the sheet wants the
build's shape. The lane brief requires quoting these lines.

## meshgen pieces (shared tools)

- **`options.shared_frame`** (Pixal3D multi-view):
  - Frames every view on one scale and one vertical centre, then bypasses the per-view background removal and
    crop.
  - Inputs must be cut at one px scale with their rows aligned.
  - Alpha cut-outs are used as the matte; otherwise BiRefNet is run.
- **`options.carve_enclosed`** (requires `shared_frame`): carves the enclosed background pockets out of the matte.
- **The default per-view path is unchanged.** It now adds `result.warnings[]` with code
  `matte_filled_enclosed_background` when the in-graph BiRefNet matte fills a background pocket the subject
  encloses, such as the wolf's horn ring.
- **Code and tests:** `meshgen/framing.py` and `meshgen/tests/test_framing.py`.

## 5. Proxy finish (prototype, 2026-10-11): the Pixal3D proxy as the BASE mesh

This is opt-in, for NEW units only. The artist's verdict on the Pixal3D trial: "all of the work it did looks better than
what we did, except for the called out issues of model interpertation". So the frozen proxy becomes the base mesh,
and the house rules are applied on top. It has been prototyped on scratch copies of holly_snowman and gemwing_dragon
only. Code: `improve/proxy_finish.py` (stage 1 + CLI), `improve/proxy_finish_build.py` (the Blender half) and
`improve/proxy_finish_parts.py` (closed procedural parts).

Commands, after intake + `sheet_proxy.py` + `new_unit_scaffold.py`:
1. `improve/proxy_finish.py init <unit>` writes three files:
   - the stub `improve/<unit>_build.py`, which the scaffolded runner runs unchanged;
   - `improve/<unit>_finish.json`, the per-unit config;
   - `palettes/<unit>/default.json`, with one region per sheet chip.
2. Edit `<unit>_finish.json`: chip names, tone merges, the tier, and the "interpretation fixes" (`straighten`, `refloor`,
   `cuts`, `parts`, rig joints). Points are `[x, y, z]` sheet units, or `{"side": [u, v], "front": [u, v]}` SHEET PX.
   `proxy_finish.py solid <unit> --out <npz>` prints the solid, melt, colour and registration report while you tune.
3. `improve/<unit>_run.ps1`: the usual digest twins, bake gate, both contract checkers, renders and numeric gate.
4. `improve/proxy_finish.py gate <unit> [--hand <glb>]` scores the finished unit against the sheet AND against the raw
   proxy, and also reports the mirror test and open edges.
5. `improve/proxy_finish.py panel <unit> [--hand <blend>]` renders sheet | raw proxy | finished | hand build.

What the finish does:
- **Solid.** The proxy soup becomes a watertight SDF: shell nodes, a closing, an exterior flood fill, and the exact
  near-band distance offset by half a voxel so blades survive.
- **Symmetry.** The SDF is averaged with its X mirror (`F_sym = (F + F_mirrored) / 2`; no bisect). The HIGH is the +X
  half, polygonised, mirrored and welded, so the mirror test is exactly 0.
- **Low and bake.** COLLAPSE decimation with `use_symmetry` gives the LOW. Normal + AO are baked against the FLAT low.
- **Colour.** Sheet pixels are projected onto the high, with an ortho z-buffer per view. The proxy texture fills
  unseen faces. Colours snap to the chips; small islands are merged, and region borders are iso-cut into the low
  (`colour.mode: facet` keeps one flat tone per facet instead, for crystal units). Small high-contrast flat sheet
  marks (eyes, buttons) become closed conforming decals automatically.
- **Parts.** `silhouette_fan` places crystal blades along the SHEET's own side and front wing silhouette.
- **Rig and clips.** The rig comes from config joints. Clips are PLACEHOLDERS.

Measured (scratch, mean IoU vs the sheet over front / back / side):

| unit | raw proxy | finished | current hand build |
|---|---|---|---|
| holly_snowman | 0.919 | 0.919 | 0.862 |
| gemwing_dragon | 0.807 | 0.762 | 0.631 |

Both units: digest twins identical, every contract check passes except the report-only cell_fit, 0 open edges, and
the mirror test is 0. Still manual:
- part placement;
- cut boxes;
- `refloor` windows;
- rig joints;
- the choice between replacing melted parts and overlaying them (see the lane report).

Kit gaps found:
1. `sheet_proxy.normalise` puts the floor at the lowest point. On gemwing that is the tail curl, which leaves the feet
   0.46 u up and the body 9 % small. The finish's `refloor` works around it.
2. The scaffolded compose's `GATE bake_reference` reads only `<unit>_build.py`, so for a stub build it prints `none`.
   It should follow the stub into `proxy_finish_build.py`, which does bake against the FLAT low.

### 5b. Light mode and repose (2026-10-11, after the artist's pick)

The artist preferred the RAW proxy for gemwing ("in trying to fix the symmetry and make changes we broke some things,
like the mouth"). Set `"mode": "light"` in `<unit>_finish.json` to keep the proxy's geometry and texture
(`proxy_finish_light.py`):
- a watertight remesh on a fine grid (no smoothing, symmetrising, straightening, cuts or parts), then a collapse
  decimation to `body_tris`; the `light.dense` boxes (head / face) are calibrated to `density` x the rest's triangles;
- normal, AO and the proxy texture (albedo) are baked from the PROXY ITSELF onto the FLAT low. The glb carries
  `basecolor = albedo x AO` as its base-colour texture, times one white palette region (`Col`), so colour lives in a
  texture, not in the palette;
- `refloor.y_windows` puts the floor at the lowest FOOT contact, not at a tail curl.

`"repose"` (opt-in, `proxy_finish_repose.py`) straightens a posed chain with bones instead of reshaping geometry. It
skins the low with the contract rig's weights, yaws each target bone to a heading (`+y` tail, `-y` head; `distribute`
spreads a head turn up the neck), and lifts a chain that dips below the floor. A `rigid` box pins the face to its bone,
so the face moves as a rigid body. The result is applied as the rest mesh, and the bakes ride along on the UVs. The
report gives edge stretch, self-intersections, the rigid-face check and the mirror test before and after.
`proxy_finish.py gate <unit> [--ref <glb>]` adds the IoU against the artist-picked version;
`panel <unit> [--ref <blend>]` renders sheet | picked | new, with a face row in light mode.

#### Fast iteration flags (light mode, 2026-10-26)

A texture tweak used to cost a whole build (vampwarrior: ~10 min, most of it recomputing the same geometry). These build
flags are opt-in: without them nothing is read or written and the outputs are byte-identical. Pass them after `--`:
- `--geo-cache` saves the HIGH + LOW step (remesh, the pre / dense / fine decimations, `smooth_region`) and reuses it
  when the inputs match exactly. The key hashes the grids, the proxy mesh, the config keys that step reads, the kit
  sources and the Blender version, so a geometry change or a kit edit always rebuilds.
- `--retexture` (implies `--geo-cache`) also saves the raw bakes, before any 2D step. On a match, Cycles is skipped
  and only the 2D steps run again (`despeckle`, `recolour`, `uv.dilate_px`, `paint_project`,
  `smooth_region.albedo_blend`, `ao_fill_below`, `smooth`, `normal_flatten`). Normal + AO and the albedo have separate
  keys, so a stage-1 texture tweak (`recolour3d`, `erase3d`, `face_paint`) re-bakes the albedo only.
- `--preview-bake <blend>` is a `--preview` that does bake, at `bake_res [2048, 1024]`, `albedo_res 2048` and
  `ao_samples 16` (never above the config). The UVs are the full build's. The textures are packed into the preview
  blend (and written to `<blend>_textures/`). There is no rig and no clips.
- `--solid <npz>` (existing) skips stage 1 when only post-stage-1 keys changed: `%TEMP%\<unit>_solid_main.npz` is the
  last main build's solid.
- `light.fine_pre_factor` (config) is the log10 factor where the `light.fine` pre-collapse bisection landed (vampwarrior:
  -2.125, the last `low.fine.pre.trials` row at or above `want_share`). The factor is tried first and kept only when it
  brackets the share, i.e. when it is the cell the bisection ends in. Then 2 decimations replace 7. Otherwise the kit
  bisects as before.

Measured on vampwarrior (an iris recolour, full builds run alone): the plain build took 516 s and `--retexture` took
198 s, with the same combined digest (cffe5c205ab24008). What remains is stage 1 (~87 s, which `--solid` skips) and the
2D steps at 4096 (~96 s).

Cache entries live in `%TEMP%\forge_light_cache` (the newest 2 per unit and kind). The ship build stays the plain
`<unit>_run.ps1` (digest twin + bake gate). The flags are for the look loop between ship builds.
