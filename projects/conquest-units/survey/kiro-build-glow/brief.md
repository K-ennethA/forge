# Lane brief: keep _GLOW on self-exporting builds

Law: C:/Users/kenne/OneDrive/Desktop/git/forge/docs/lane-conventions.md — read first; its rules bind you.

DELIVERABLE: the 8 self-exporting unit builds keep the float _GLOW attribute on their next rebuild, so rebuilding never regresses the shipped glbs.

CONTEXT: projects/conquest-units/improve/export_glb.py is the shared exporter: before export it adds an in-memory float copy of the Glow vertex-color rgb as attribute "_GLOW" and exports with export_attributes=True (read its docstring + code first; its gate requires _GLOW exact, exit 0 = pass). The 8 builds that export their own glb are improve/{duskmaw,firefly,firesprite,magmoo,supaoctto,vampito,vampwarrior,wren}_build.py — their export calls lack the _GLOW step.

TASK: refactor so all 8 build scripts produce glbs with _GLOW, by the DRYest shape available: prefer importing/reusing the shared helper from export_glb.py (both run inside Blender; sys.path append is fine) over 8 pasted copies. Do not change any geometry, material, rig, or animation logic — export path only.

FILES allowlist (writes): improve/duskmaw_build.py, firefly_build.py, firesprite_build.py, magmoo_build.py, supaoctto_build.py, vampito_build.py, vampwarrior_build.py, wren_build.py, and export_glb.py ONLY if extracting a reusable function requires it (behavior must stay identical — its own gate must still exit 0 on an already-correct glb). NEVER touch improve/elias_* or rigged/elias.* (another lane owns them). rigged/<unit>.glb may be overwritten ONLY by the one gate rebuild below and must end _GLOW-equal to the committed version.

EVIDENCE/GATE: (1) rebuild ONE unit end-to-end — vampito or firefly, whichever runner (improve/<unit>_run.ps1) is cheaper per its docstring — and show its fresh glb passes export_glb.py's gate (exit 0, _GLOW exact) and that _GLOW max matches the committed value (firefly 1.60; vampito report measured). (2) For the other 7: show by diff/static check that the export path now routes through the _GLOW-adding code. Quote the numbers.

RULES: Blender is at "C:/Program Files/Blender Foundation/Blender 5.0/blender.exe", always run with --background --factory-startup, headless only, nothing windowed, never commit, never push, never touch port 9876. Handback <= 40 lines: files touched, gate unit + its numbers, static-check result per remaining unit.
