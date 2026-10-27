# Handoff: Duskmaw (two forms) + Vampire Warrior re-run, 2026-10-25

This covers the 2026-10-24/25 sessions. NOTHING is committed. Every artist note is quoted verbatim in
`design/review-log.md`, in the entries dated 2026-10-24 and 2026-10-25; read them first.

## Where we are

The artist rejected the last builds of both units as "clumpy, like poorly sculpted", and said Duskmaw
"looks destroyed on parts". An independent Codex review and a stage-by-stage diagnostic both found the
same root cause.

- **The watertight voxel remesh in the light finish (stage 1) destroys detail.**
  - The grid step is about 0.025 u, and every surface is pushed out half a step.
  - It fuses hair strands into tubes, erases eyelids and puffs up lips.
  - It also stair-steps and pimples the Duskmaw maw.
- **Decimation adds a second problem on Duskmaw:** the whole maw gets only about 3k tris, so each tooth is 1–2 facets.
- **Patching on top could not fix this.** Decision (artist: "make the fixes"): RE-RUN with a finer
  remesh grid in the detail areas, from new, cleaner generation inputs.

Evidence: `design/codex-review-2026-10-25-duskmaw-vampwarrior.md` (the first Codex review). The second
review and the diagnostic sheets are in the scratch folders listed below.

## Plan (in order)

1. **Kit: an opt-in finer remesh grid inside named boxes** (`light.fine` or similar, `div` 2–3). The same
   sign / closing / offset rule runs at step/div, is stitched into the coarse solid, and keeps its
   triangle share through the two-stage decimation. With the key absent, nothing changes.
   - **In progress when the session ended:** a diagnostic agent was writing this patch in
     `C:\Users\kenne\AppData\Local\Temp\claude\C--Users-kenne-OneDrive-Desktop-git-forge\da2aa1fe-9826-4887-a170-2ed983961ce7\scratchpad\diag\`.
     Its prototype is `fine_solid.py`. If no finished `*.patch` is there, write it fresh from fine_solid.py.
   - **Measured:** step/2 cuts the share of the vamp face more than 1 step off the source from 7.9 % to
     1.8 %, and the duskmaw maw from 9.9 % to 1.3 %.
   - **Cost:** a global step/2 is fine for vamp (about 43 M nodes) but too heavy for duskmaw (about
     128 M nodes), so it has to be per box.
   - **Gate:** a `--digest-only` build of vulture_creature must still give **5b3aaa03830de189**.
2. **Vampire Warrior: head-only re-run.**
   - **Keep:** the v5 body (proxy `design/reference/vampwarrior/proxy/` sha 3f0ae1b3, 4096 texture, 400k faces).
   - **Replace:** the WHOLE head, hair included, with **head5** (`design/reference/vampwarrior/head_graft/head5_pixal3d.glb`,
     sha 7d5be5b4b64a). It is a 4-view Pixal3D of the Codex references in `head_graft/codex_refs_v2/`: a
     calm closed mouth with two small fangs, clean grouped locks and a real profile. Preview: `head5_views.png`.
   - **Remove the proxy's own turned head cap completely.** The diagnostic found a second diagonal skull outline under the graft.
   - **Run** the light finish with fine boxes on the head and face.
   - **Drop** the stacked v5.x repaint patches that fought the remesh (paint_project re-fits, hair darks
     fill, and so on). Re-add only what is still needed.
   - **Head5's small flaws:** the face-framing locks flare a little like a hood, and the painted eye outlines are thick.
   - **Keep from v5.3:** the hero tier (30–50k), the traced sword (light.parts), the low wedge flat boots,
     smooth face and hair (skin_ / hair_ regions) and the face UV island.
3. **Duskmaw base: full re-run** from the new clean proxies in `design/reference/duskmaw/proxy_v6/`
   (Codex turnarounds in `design/reference/duskmaw/codex_refs/`):
   - `duskmaw_closed_v6.glb` (sha 92da035a09ce): sleek and clean. **This is the base mesh.**
   - `duskmaw_open_v6.glb` (sha 990da382a90b): clean, but Pixal3D only PAINTED the open mouth; no cavity.
   - **Mouth (spec, not a choice):** it opens and closes; idle alternates closed and open (artist,
     2026-10-24). Build method (decided):
     - carve a shallow maw cavity into the closed mesh (light.cuts) with a dark-red interior;
     - add the upper and lower tooth rows as glowing closed parts traced from the open sheet;
     - add a body-tone lid carrying the traced tear (v5.4's `duskmaw_parts.py` tear) that covers the cavity when closed;
     - bones scale lid / tear vs teeth / cavity between the two states. Reuse v5.4's no-shell chains, scale keys and clips.
   - **Fine boxes** on the head and maw; body_tris about 22k, with a real maw share. Keep it flat-shaded,
     except a skin_face smooth region if needed.
   - **Wiring:** the finish reads `design/reference/duskmaw/` (the open-sheet intake). Swap the proxy block
     to the v6 closed glb (score it with `improve/sheet_proxy.py duskmaw --glb <glb> --out-dir <scratch>
     --no-write-intake`, then merge the block, as before). The closed-sheet intake is in `design/reference/duskmaw_closed/`.
4. **Duskmaw true form:** a fine-grid re-run. The Codex review also calls the body a "skeletal cage", the
   arms thin loops and the closed mouth a crowded tooth bundle. The artist has not reviewed v1.1 yet.
5. **Show each re-run to the artist before the next one.** Ask only movement + height (OPEN-QUESTIONS
   "Duskmaw (two forms)" and "Vampire Warrior re-run"); every clip is still a PLACEHOLDER.
6. **Then offer the commit + push** (origin + backup), scoped to these units, the kit and the logs.

## Unit status (last full runs; all green except the report-only cell_fit)

| unit | version | digest | config / state |
|---|---|---|---|
| duskmaw | v5.3.2 shipped; v5.4 config in place (sandbox, not full-run) | 5b4b0bd9d2d855cc | improve/duskmaw_finish.json, duskmaw_parts.py, duskmaw_STATE.md |
| duskmaw_true_form | v1.1 | 46e5e5268b5132c1 | improve/duskmaw_true_form_finish.json, _STATE.md |
| vampwarrior | v5.3 | 9d9bb8ee724adf74 | improve/vampwarrior_finish.json, vampwarrior_parts.py, _STATE.md |

- **Two forms = two separate models,** swapped in game under a VFX, not a morph: base `transform_out`, true form `transform_in`.
- The old procedural duskmaw v4 and vampwarrior v4.2 are in git history (6b9e2e1 / 0a6da6f).

## Kit changes made this session

All opt-in. vulture_creature reproduced 5b3aaa03830de189 after every one.

- **proxy_finish_build.py:**
  - `clips.extra` keyed-pose clips (rot / loc / scale keys; an extra named "idle" replaces the kit idle);
  - the rig chain `"shell": false` option (the chain carries only parts).
- **proxy_finish.py / proxy_finish_light.py:**
  - `light.parts` (+ `replace_cuts`);
  - `light.uv` (named islands, density, dilation);
  - `light.smooth` (smooth palette regions);
  - `light.paint_project`;
  - `head_graft.clip_q2` (side clip);
  - the glow3d init fix (keeps the parts and smooth regions).
- **rigkit.py:** new ALLOWED_CLIP_NAMES (chomp / close / transform_in / transform_out) + ONESHOT_CLIP_NAMES.
- **conquest_contract_check.py:** non-looping one-shot clips are allowed, and SMOOTH_OK_PREFIX is ("hair", "skin").

## How things were run (reuse)

- **Python:** `service\.venv\Scripts\python.exe -P`. Note: `-P` drops cwd from sys.path, so `python -m meshgen` fails.
- **ComfyUI** (needed for BiRefNet + Pixal3D): start it with `scratchpad\warm_comfy.py` (ComfyUIClient.ensure_running).
  - Free VRAM between jobs: `curl -X POST localhost:8188/free -d '{"unload_models":true,"free_memory":true}'`.
  - Direct multi-view generation: Pixal3DBackend.generate(None, {"seed":56, "shared_frame":True,
    "texture_resolution":4096, "views": multiview.normalise_views({...})}, out). Scripts:
    `head_graft/gen.py`, `proxy_v6/gen.py`, `vampwarrior/proxy/gen_hi.py`.
- **Codex** (reference images + visual reviews): `C:\Users\kenne\AppData\Roaming\npm\codex.cmd exec
  --skip-git-repo-check -C <dir> -s workspace-write -i <img>... -o last.txt - < prompt.txt`.
  - Images land in `<dir>`.
  - Its image tool refused prompts with "teeth / maw / devour" wording; the stylised "glowing chest pattern" wording passed.
  - Prompts are saved next to each reference set.
- **Light-flow lanes:** each unit's `improve/<unit>_lane_brief.md` holds the full brief. Lanes write kit
  patches and stop; the orchestrator reviews, applies, and runs the vulture regression.
- **Raw-mesh look:** the `scratchpad\rawlook\r.py` Blender workbench render (untextured + textured, front / 3/4 / side).

## Scratch (session da2aa1fe; may be cleaned)

All under `C:\Users\kenne\AppData\Local\Temp\claude\C--Users-kenne-OneDrive-Desktop-git-forge\da2aa1fe-9826-4887-a170-2ed983961ce7\scratchpad\`:

- `diag\` — diagnostic stage sheets + fine_solid.py
- `codex_review2\review.md` — the second Codex review
- `duskmaw_lane\`, `vampwarrior_lane\` — patches, generators, sandboxes

Everything needed going forward has been copied into `design/reference/` (proxies, heads, Codex refs).
