"""Scaffold a NEW from-scratch Conquest unit (plain Python, no Blender): step 2 of .claude/skills/conquest-new-unit.

    python -P new_unit_scaffold.py <unit> [--force]

Stamps the unit-agnostic half of the firefly pattern out of improve/templates/new_unit/ (the {{UNIT}} token replaced):
    improve/<unit>_run.ps1        the one-command runner (build + digest twin + bake gate + checks + renders + compose)
    improve/<unit>_render.py      survey stills, focus close-ups, sheet-scale orthos (framed by intake.json)
    improve/<unit>_clips.py       per-clip mp4 + contact sheet
    improve/<unit>_compose.py     sheet-vs-model + contact sheet (crops from intake.json)
    improve/<unit>_bake_diff.py   the pinned two-run bake tolerance gate
    improve/<unit>_STATE.md       the per-unit STATE skeleton (lane-conventions 'context economy')
and makes palettes/<unit>/ + renders/<unit>/. The unit-SPECIFIC half -- <unit>_build.py, <unit>_parts.py and
palettes/<unit>/default.json -- is the build lane's deliverable, written to templates/new_unit/BUILD_CONTRACT.md.
Existing files are never overwritten without --force (a re-scaffold must not clobber a tuned runner).
"""
import os, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
T = os.path.join(HERE, "templates", "new_unit")
args = sys.argv[1:]
UNIT = args[0]
FORCE = "--force" in args
assert UNIT.replace("_", "").isalnum() and UNIT.islower(), "unit slug must be lower_snake: %r" % UNIT
intake = os.path.join(ROOT, "design", "reference", UNIT, "intake.json")
assert os.path.exists(intake), "run sheet_intake.py first (no %s)" % intake

OUT = {"run.ps1.tmpl": "%s_run.ps1", "render.py.tmpl": "%s_render.py", "clips.py.tmpl": "%s_clips.py",
       "compose.py.tmpl": "%s_compose.py", "bake_diff.py.tmpl": "%s_bake_diff.py"}
done = []
for src, pat in OUT.items():
    dst = os.path.join(HERE, pat % UNIT)
    if os.path.exists(dst) and not FORCE:
        done.append((os.path.basename(dst), "kept (exists)"))
        continue
    txt = open(os.path.join(T, src), encoding="utf-8").read().replace("{{UNIT}}", UNIT)
    open(dst, "w", encoding="utf-8", newline="\r\n" if dst.endswith(".ps1") else "\n").write(txt)
    done.append((os.path.basename(dst), "written"))
state = os.path.join(HERE, UNIT + "_STATE.md")
if not os.path.exists(state) or FORCE:
    open(state, "w", encoding="utf-8").write(
        "# %s STATE (scaffolded %s)\n\n"
        "- Version: v0 (scaffold only; v1 = the first build lane's draft)\n"
        "- Spec: design/review-log.md entry 'NEW UNIT: %s' + design/reference/%s-character-sheet.* "
        "(intake: design/reference/%s/intake.json)\n"
        "- Pattern: firefly (from-scratch SDF shell + closed parts; templates/new_unit/BUILD_CONTRACT.md)\n"
        "- LOCKED: (nothing yet)\n"
        "- Knobs that matter: (the build's TUNABLE CONSTANTS block; list the artist-facing ones here)\n"
        "- Unit gates: contract checks (cell_fit report-only), digest twins, bake gate\n"
        "- Open artist questions: design/OPEN-QUESTIONS.md section '%s'\n"
        % (UNIT, datetime.date.today().isoformat(), UNIT, UNIT, UNIT, UNIT))
    done.append((os.path.basename(state), "written"))
for d in (os.path.join(ROOT, "palettes", UNIT), os.path.join(ROOT, "renders", UNIT)):
    os.makedirs(d, exist_ok=True)
for f, s in done:
    print("SCAFFOLD %-28s %s" % (f, s))
