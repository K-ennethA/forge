# glb export + glow-mask fidelity gate (import wave, 2026-10-02)

Exporter: `improve/export_glb.py` (one shared script; unit name as the argument). It uses the builds' call pattern plus explicit
`export_vertex_color='ACTIVE'` and `export_all_vertex_colors=True`, and it gates each glb against its blend. Blender 5.0.1 and
Godot 4.6.stable run headless.
Run: `blender --background --factory-startup --python-exit-code 1 --python improve/export_glb.py -- <unit> [--check-only]`.
Audit without bpy: `python improve/export_glb.py --audit rigged/*.glb`.

## Verdicts
1. **COLOR_1 is present in all 16 rigged glbs** (8 new, 8 existing). No existing glb lacked it, so nothing was re-exported
   and no `_build.py` was touched. The reason: `export_all_vertex_colors` defaults to True in Blender 5.0. The probe's
   "must set it" caveat was never measured, and it does not apply.
2. **FORMAT FACT: HDR glow is clamped at export. This affects 5 of 16 units.** Blender writes the extra colour set as normalized u16 and
   applies `np.clip(0,1)` first (io_scene_gltf2 `primitive_attributes.py` `__gather_attribute`). Palette glow with
   `emission_scale > 1` therefore loses its tiers. The glb equals clip(source) exactly (gate exit 2):
   - geode: 3 of 7 glow values are >1. G channel 1.2 / 1.4 / 1.7 all become 1.0. The core, seam and eye tiers merge, and hue shifts. 2430 triangle corners are affected.
   - duskmaw: R 2.0 becomes 1.0 (1 of 7 values, 3120 corners).
   - firefly flame: 1.11 and 1.6 become 1.0 (2 of 3 values).
   - firesprite: 1.05 to 1.4 become 1.0 (3 of 10 on the body, 1 of 4 on the wand).
   - magmoo: 1.15 becomes 1.0 (1 of 8).
   Options (the orchestrator or artist decides; nothing is implemented):
   (a) Ship a float custom attribute. MEASURED on geode: a CORNER FLOAT_VECTOR `_GLOW` with `export_attributes=True`
       exports as `_GLOW` VEC3 float, max G 1.7, all 7 distinct values intact. The extension would read `_GLOW` instead of
       COLOR_1. That is a small edit, but it has not been tested in Godot.
   (b) Pre-scale Glow by 1/k at export and multiply by k in the shader. k is stored per unit.
   (c) Accept the clamp. Tiers are then expressed only by the shader's strength uniform.
3. **ALPHA_DROPPED (existing glbs, COLOR_0):** firefly `firefly_smoke` (BLEND) and firesprite_wand `firesprite_fire` (BLEND)
   are written with COLOR_0 as VEC3. The source alpha values (0.4 to 0.86 and 0.62 to 0.95) are dropped because those
   materials read no vertex alpha. This touches godot-import-notes item 5 (translucency). The fix is in the build's material
   wiring, outside this lane's allowlist.
4. **SKINNED GATE (geode): FAIL with the probe's extension as it stood. PASS after an 8-line extension fix.**
   - Stock `color1_ext.gd`: `ImporterMesh.clear()` also drops the blend-shape names, so `add_surface` rejects the surface
     (`p_blend_shapes.size() != blend_shapes.size()`). **geode imports with an EMPTY mesh**, and the runtime
     `generate_scene` segfaults (`import_log_stockext.txt`, `dump_log_geode_stockext.txt`). This hits every unit with
     morph targets: geode (2), magmoo (7), supaoctto (1).
   - Fixed extension (re-declares the blend-shape names and mode before re-adding surfaces): **CUSTOM0 = COLOR_1 on
     16934/16934 vertices, 0 mismatches, 0 unmatched**, on both the editor-import and runtime paths. Also intact: skin,
     Skeleton3D (8 bones), 2 blend shapes, BONES/WEIGHTS, and the idle/walk clips.
   - Multi-surface check: firesprite (2 meshes, 4 primitives, 37 bones, skinned) gives **50959/50959 matches, 0 mismatches**
     on both paths, using primitive order equal to surface order. Godot reorders vertices on editor import (only 12126/33799
     match by index), so the check matches by position, normal and UV.
   - Negative control: with shuffled CUSTOM0, geode shows 7816/16934 mismatches and firesprite 25541/50959, so the
     comparator discriminates.
   - Files: `survey/color1_probe/probe_project/{dump_unit.gd, compare_unit.py, compare_geode.txt, compare_firesprite.txt}`.
     The CUSTOM0 values are the glb's (already clamped) values.

## Per-unit table (gate = `--check-only` against the source blend; COLOR_0 and COLOR_1 are present on every primitive)
| unit | source | MB | tris | prims | joints | anims | morphs | verdict |
|---|---|---|---|---|---|---|---|---|
| vineweave (new) | rigged/vineweave.blend | 12.26 | 30000 | 1 | 24 | idle | 0 | EXACT |
| blightcap (new) | rigged/blightcap.blend | 1.14 | 5000 | 1 | 9 | idle/walk | 0 | EXACT |
| petalfang (new) | rigged/petalfang.blend | 1.15 | 4978 | 1 | 12 | idle/walk | 0 | EXACT* |
| barkling (new) | rigged/barkling.blend | 1.14 | 5000 | 1 | 12 | idle/walk | 0 | EXACT |
| mycothrall (new) | rigged/mycothrall.blend | 0.93 | 4000 | 1 | 13 | idle/walk | 0 | EXACT |
| eldroot (new) | rigged/eldroot_standing4.blend | 12.08 | 28996 | 1 | 21 | idle/sit_down/sitting_idle/stand_up/walk | 0 | EXACT |
| geode (new) | rigged/geode.blend | 1.46 | 5704 | 1 | 8 | idle/walk | 2 | HDR_CLAMPED |
| mortis (new) | rigged/mortis.blend | 13.70 | 38640 | 1 | 27 | idle | 0 | EXACT |
| duskmaw (kept) | rigged/duskmaw.blend (v4) | 11.93 | 29398 | 1 | 19 | idle/walk | 0 | HDR_CLAMPED |
| firefly | rigged/firefly.blend | 5.04 | 20762 | 3 | 21 | idle/walk | 0 | HDR_CLAMPED+ALPHA_DROPPED |
| firesprite | rigged/firesprite.blend | 5.66 | 17576 | 4 | 37 | idle/walk | 0 | HDR_CLAMPED+ALPHA_DROPPED |
| magmoo | rigged/magmoo.blend | 19.51 | 49192 | 1 | 33 | ball/idle/walk | 7 | HDR_CLAMPED |
| supaoctto | rigged/supaoctto.blend | 6.57 | 22336 | 1 | 51 | float/idle/walk | 1 | EXACT |
| vampito | rigged/vampito.blend | 4.36 | 10696 | 1 | 8 | idle/walk | 0 | EXACT |
| vampwarrior | rigged/vampwarrior.blend | 13.57 | 48150 | 2 | 96 | idle/walk | 0 | EXACT |
| wren | rigged/wren.blend | 12.07 | 48853 | 2 | 94 | idle/walk | 0 | EXACT* |

*The exporter drops sliver triangles (petalfang: 4980 tris in the blend, 4978 in the glb). The one Col value that
was lost sits on a single 3.4e-7 m² triangle. The gate reports values that exist only on triangles under 1 mm² and does not fail on them.
Encoding in every glb: COLOR_0 is VEC3 float when the material reads no vertex alpha (otherwise VEC4 u16n), and COLOR_1 is VEC4 u16n.

## Deviations from the brief (measured)
- **duskmaw: v3 was NOT exported.** `rigged/duskmaw.blend` and `rigged/duskmaw.glb` are **v4**. Evidence: the
  `duskmaw_build.py` docstring, both files written at 2026-09-26 12:03, and the glb's 29398 tris equal `check_duskmaw.json`
  (v3 has 28800). The review-log says "duskmaw is much better and fine for now" after v4. `duskmaw_v3.*` are the kept
  "before" outputs, so exporting v3 would have regressed to a rejected version. The audit's "v1-era name" claim was wrong.
- eldroot: `eldroot_standing4.blend` is the current version. Its height is **4.561 m (the 0.895x auto-refit)**. The review-log says the SHIP build
  uses `--no-cell-refit` (5.10 m). Proportions are identical, but the import scale must allow for this, or the unit
  should be rebuilt with `--no-cell-refit`.
- Determinism and parity: re-exporting all 8 new units gives byte-identical glbs. Re-exporting firefly with export_glb.py is
  byte-identical to its build's own glb, which shows the call pattern matches the builds.
- Not exported: skin variants (`*__<skin>.blend`). Not in scope.
- Emission-pulse keys (geode, mycothrall) are absent in ACTIONS mode, as before (godot-import-notes item 2 fallback).
