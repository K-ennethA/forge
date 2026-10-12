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
