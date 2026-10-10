---
name: conquest-new-unit
description: Kick off a NEW from-scratch Conquest unit/creature from an artist's character sheet (three-view turnaround image) using the firefly pattern - intake (save + measure the sheet, sample the palette), scaffold the unit's runner/render/compose, log the spec, dispatch ONE background build lane, then gate and show the renders. Use when the artist hands over a character reference sheet and asks for "the model for this", "a new unit/creature", "build this character", or names the firefly/firesprite pattern.
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

## 1. Intake (orchestrator, ~1 min)
1. Copy the sheet to `design/reference/<unit>-character-sheet.<ext>` (never edit the original).
2. `python -P improve/sheet_intake.py design/reference/<unit>-character-sheet.<ext> <unit>`
   (`--views` if the sheet's order is not front,back,side; `--bg-tol` if views fuse).
3. LOOK at `design/reference/<unit>/intake_overlay.png`: one cyan box per view, magenta axis
   through the base, yellow floor line, the swatch chips sampled. A wrong split is fixed HERE
   (rerun with flags) — never passed to the lane. Known traps (shadowbun, 2026-10-09):
   - a view drawn THREE-QUARTER (many sheets' "front") is not an ortho: rerun with
     `--ortho-views back,side` (or whichever views are true orthos) so the compare uses only those;
   - an axis pulled by a low tail / prop: `--base-frac` smaller, or `--axis view=x_px`;
   - palette labels may sit off their chips: map chips by COLOUR, never by label row.
4. Look at the sheet yourself and transcribe it: parts, materials, the detail panels'
   notes, palette, anything that will need a deviation or an artist decision.

## 2. Scaffold (orchestrator, seconds)
`python -P improve/new_unit_scaffold.py <unit>` -> improve/<unit>_{run.ps1, render.py,
clips.py, compose.py, bake_diff.py, STATE.md} from improve/templates/new_unit/ (never
overwrites without --force).

## 3. Log the spec (orchestrator)
- Append to `design/review-log.md`: `## <date> — NEW UNIT: <Title> (artist reference sheet)`,
  the reference path, the ARTIST VERBATIM, then a **Binding reading**: the sheet transcription
  (body plan, parts, palette sampled values, detail-panel notes), tier + window, every
  deviation the artist asked for, and what ships as PLACEHOLDER (usually the clips: movement
  intent is asked, never guessed — idle + walk placeholders keep clip_names green).
- Add a `## <Unit>` section to `design/OPEN-QUESTIONS.md` with numbered questions, each with
  its live default and knob (movement intent, tier/height in the game, any sheet ambiguity).
- Fill the STATE skeleton's spec line.

## 4. Dispatch ONE lane (background)
Fill `improve/templates/new_unit/lane_brief.md.tmpl` ({{UNIT}}, {{TITLE}}, {{DATE}}, {{SHEET}},
{{DELTA}} = the unit's body-plan differences from the firefly in a few lines: which parts are
SDF vs closed parts, rig chains, placeholder clips, tier window, skins) and launch it with the
Agent tool: `subagent_type: general-purpose`, `model: opus`, `run_in_background: true`,
description `<unit> v1 build lane`. Tell the artist in one or two lines what was kicked off
and what they will get back. Close your turn — the lane's notification resumes you.

## 5. Gate + show (orchestrator, on the lane's notification)
1. Read `improve/log_<unit>_run.txt` / the digest line yourself (never trust the report alone):
   DIGEST identical (or except-bakes gate PASS); `rigged/check_<unit>.json` failures are only
   report-only cell_fit (and clip_names if a clip is genuinely absent).
2. LOOK at `renders/<unit>/<unit>_sheet_vs_model.png` and `<unit>_contact.png`. A visible
   departure from the sheet the lane did not report = resume the lane once (defect at gate).
3. Show the artist the two images (SendUserFile, render) + a short numbers summary + the
   OPEN-QUESTIONS for this unit. Update `improve/<unit>_STATE.md`.
4. Offer the commit (scope `git add` to the unit's files + the log/questions entries; the
   commit message style is `conquest-units: <Unit> v1 draft (new unit, sheet <date>)`).
   Commit only when the user says so.

## Artist feedback afterwards
Parameter tweaks: edit the build's TUNABLE constant directly + rerun the runner (no lane).
Structural changes: a resume of the same lane (one bounded follow-up) or a new lane briefed from
this skill's template + the STATE file + the new review-log entry.
