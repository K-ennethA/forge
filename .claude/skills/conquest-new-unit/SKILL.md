---
name: conquest-new-unit
description: Kick off a NEW from-scratch Conquest unit/creature from an artist's character sheet (three-view turnaround image) using the firefly pattern - smart intake (save + measure the sheet, auto-pick views/floor/axis/facing, sample the palette), a Pixal3D sheet proxy as a 3D reference when the GPU is free, scaffold the unit's runner/render/compose, log the spec, dispatch ONE background build lane, then gate (numeric sheet gate) and show the renders. Use when the artist hands over a character reference sheet and asks for "the model for this", "a new unit/creature", "build this character", or names the firefly/firesprite pattern.
argument-hint: <sheet image path | 'attached'> <unit_slug> [notes / artist verbatim]
---

# Conquest new unit — the kickoff process

You are the ORCHESTRATOR. You run the cheap deterministic stages yourself, and hand the
modelling to ONE background lane. You do NOT write the unit's build script yourself.
Law: docs/lane-conventions.md (lane contract, WIP <= 2 implementation lanes, agents never
commit or spawn, nothing windowed, port 9876 never). Paths below are relative to
`projects/conquest-units/`. Python = `C:\Users\kenne\OneDrive\Desktop\git\forge\service\.venv\Scripts\python.exe -P`.

## 0. Inputs
- Sheet: a path, or the image attached to the message (its source path is in the image tag).
- `<unit>`: lower_snake slug (sheet title, e.g. "SEEDANT" -> `seedant`). If absent, derive it
  from the sheet title and say so.
- Artist verbatim: every word they said about this unit (it goes in the log VERBATIM).

Flow (docs/conquest-new-unit-flow.md): SMART INTAKE -> SHEET PROXY (when the GPU is free) -> scaffold
(pre-filled brief) -> ONE build lane citing the proxy sections -> NUMERIC SHEET GATE. House rules stand: the
sheet is the spec, the rest pose is symmetric, ask only movement + height.

## 1. Intake (orchestrator, ~1 min)
1. Copy the sheet to `design/reference/<unit>-character-sheet.<ext>` (never edit the original).
2. `python -P improve/sheet_intake.py design/reference/<unit>-character-sheet.<ext> <unit>`
   (`--views` if the sheet's order is not front,back,side). SMART by default: it removes the
   title / rule lines / detail panels, splits views fused by touching parts along the weakest
   contact, mattes a dark subject on a dark sheet with BiRefNet (meshgen's ComfyUI, when up),
   picks the floor (lowest body contact: drop shadows, thin tail curls, hanging tails and
   floating parts ignored), the axis (mirror symmetry front/back, contact centre side), the
   ortho views (3/4 fronts/backs flagged out), the side facing (`side_facing`, image-left =
   meshgen `left`), a chip-row palette fallback, and the front-vs-side height disagreement.
   It prints `AUTO_FLAGS` (the equivalent flags) and `NOTE` lines, and writes them to
   intake.json `intake_notes` (they go into the lane brief verbatim). Every flag you pass wins
   over its auto value; `--no-smart` is the exact pre-smart intake.
3. LOOK at `design/reference/<unit>/intake_overlay.png`: one cyan box per view (3/4? / ORTHO tags),
   magenta axis, yellow floor line (orange = the raw lowest row it ignored), the side-facing tag,
   the swatch chips, the notes at the bottom. A wrong split is fixed HERE (rerun with the flag
   the note names) — never passed to the lane. A WEAK side-facing note means LOOK at the head and
   pass `--side-facing left|right`; a 3/4 SIDE is not auto-detected (pass `--ortho-views`).
   Known traps (shadowbun, 2026-10-09; the flags still work as overrides):
   - a view drawn THREE-QUARTER (many sheets' "front") is not an ortho: rerun with
     `--ortho-views back,side` (or whichever views are true orthos) so the compare uses only those;
   - an axis pulled by a low tail / prop: `--base-frac` smaller, or `--axis view=x_px`;
   - palette labels may sit off their chips: map chips by COLOUR, never by label row;
   - a big TITLE or the detail panels merging into a view: `--region x0,y0,x1,y1` (the turnaround
     box only; chips are still searched sheet-wide);
   - views that OVERLAP in x (a side-view beak over the back view's feathers, touching tentacles):
     `--assign x1,x2` (each blob goes to the view holding its centre).
   Chips are found as a column OR a row, with gaps and dark chips recovered by spacing.
0. Sheets pasted inline (no file on disk): run a clipboard watcher (save each NEW clipboard image to
   design/reference/incoming/) and ask the artist to copy each sheet image; identify each by its title.
4. Look at the sheet yourself and transcribe it: parts, materials, the detail panels'
   notes, palette, anything that will need a deviation or an artist decision.

## 1b. Sheet proxy (orchestrator, ~4 min of GPU, background)
`python -P improve/sheet_proxy.py <unit>` — Pixal3D 3-view (seed 56, meshgen shared_frame) of the
sheet's own cut-outs, frozen with its sha under `design/reference/<unit>/proxy/`, normalised to the
sheet frame, scored (per-view IoU, row error, side length / front height), cross-sections at 8 height
bands + landmark depths written to intake.json `proxy`, panel `proxy/<unit>_proxy_vs_sheet.png`.
- Exit 3 = not enough free VRAM (~8.6 GB): nothing generated - skip it (the lane works from the sheet)
  or close GPU apps and rerun. Never block the unit on it.
- Gate: mean IoU >= 0.75 and side length within 5% = "reliable"; else "proxy unreliable, work from
  the sheet" (the block is still written so the lane sees why).
- It is a 3D REFERENCE ONLY (option ii): the build stays procedural. LOOK at the panel.

## 2. Scaffold (orchestrator, seconds)
`python -P improve/new_unit_scaffold.py <unit>` -> improve/<unit>_{run.ps1, render.py,
clips.py, compose.py, bake_diff.py, STATE.md, lane_brief.md} from improve/templates/new_unit/ (never
overwrites without --force). Run it AFTER the proxy (or after deciding to skip it): the brief draft is
pre-filled with the intake notes and the proxy verdict.

## 3. Log the spec (orchestrator)
- Append to `design/review-log.md`: `## <date> — NEW UNIT: <Title> (artist reference sheet)`,
  the reference path, the ARTIST VERBATIM, then a **Binding reading**: the sheet transcription
  (body plan, parts, palette sampled values, detail-panel notes), tier + window, every
  deviation the artist asked for, and what ships as PLACEHOLDER (usually the clips: movement
  intent is asked, never guessed — idle + walk placeholders keep clip_names green).
- Add a `## <Unit>` section to `design/OPEN-QUESTIONS.md` with ONLY movement intent and in-game
  height (artist rule 2026-10-09: the SHEET is the spec - never ask about or add anything the
  sheet does not draw: weapons, wings, glow, extra parts; report deviations from the sheet instead).
- Fill the STATE skeleton's spec line.

## 4. Dispatch ONE lane (background)
Finish the scaffolded `improve/<unit>_lane_brief.md` ({{UNIT}}, {{SHEET}}, {{INTAKE_NOTES}} and {{PROXY}}
are already filled; fill {{TITLE}}, {{DATE}}, {{DELTA}} = the unit's body-plan differences from the firefly
in a few lines: which parts are SDF vs closed parts, rig chains, placeholder clips, tier window, skins)
and launch it with the
Agent tool: `subagent_type: general-purpose`, `model: opus`, `run_in_background: true`,
description `<unit> v1 build lane`. Tell the artist in one or two lines what was kicked off
and what they will get back. Close your turn — the lane's notification resumes you.

## 5. Gate + show (orchestrator, on the lane's notification)
1. Read `improve/log_<unit>_run.txt` / the digest line yourself (never trust the report alone):
   DIGEST identical (or except-bakes gate PASS); `rigged/check_<unit>.json` failures are only
   report-only cell_fit (and clip_names if a clip is genuinely absent); the numeric sheet gate
   (`improve/log_<unit>_compose.txt` GATE lines / `renders/<unit>/<unit>_sheet_gate.json`): per-view
   IoU + row error, the proxy's IoU beside it, `flagged=` views (> 0.05 below the proxy: the lane fixed
   them or said why), `GATE bake_reference=flat`.
2. LOOK at `renders/<unit>/<unit>_sheet_vs_model.png` and `<unit>_contact.png`. A visible
   departure from the sheet the lane did not report = resume the lane once (defect at gate).
3. Show the artist the two images (SendUserFile, render) + a short numbers summary (include
   the gate's mean IoU) + the OPEN-QUESTIONS for this unit. Update `improve/<unit>_STATE.md`.
4. Offer the commit (scope `git add` to the unit's files + the log/questions entries; the
   commit message style is `conquest-units: <Unit> v1 draft (new unit, sheet <date>)`).
   Commit only when the user says so.

## Artist feedback afterwards
Parameter tweaks: edit the build's TUNABLE constant directly + rerun the runner (no lane).
Structural changes: a resume of the same lane (one bounded follow-up) or a new lane briefed from
this skill's template + the STATE file + the new review-log entry.
