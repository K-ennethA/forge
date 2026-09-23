# Organic shapes never go through part.py

## WHEN

The deliverable is an organic form — a character part, an animal feature, a
sculptural shape from a picture (ears, a tail, fur, a face). No mechanical
interface is being asked for, or the mechanical bits are separate parts.

## THE RECIPE

1. Pick the lane FIRST, before writing anything: organic form → Blender
   (generate from the reference crop with `import_generated`, or block out a
   primitive and shape it with `sculpt_brush` / `remesh`), never a
   Build123d `part.py`. Lofting an ear out of parametric curves is
   re-deriving a sculpt with the wrong tool.
2. The moment the shapes exist in the scene — before polish, before perfect
   walls — **save the deliverable** (`save_project_blend` or the export the
   task names). Then keep improving and re-save. A timeout must catch you
   with a rough deliverable on disk, not with nothing.
   **A save you did not verify did not happen.** The save tool's answer
   carries `path`, `size` and `exists` — quote them. If the call errors
   (commonly: no project attached — pass `project` explicitly), fix and
   retry; never summarize a save you have no receipt for. Measured: a run
   said "checked, and saved into bench-ear-sculpt.blend" while no such file
   existed anywhere on disk, and scored 0 for it.
3. Verify by numbers as you go (`probe`, `mesh_diagnose`, the check stage),
   render once at the end for appearance.

## WHY (measured)

Benchmark `ear-sculpt`, 2026-09-23, two runs (`benchmark/results/`): both
agents chose part.py for a pair of Eevee ears from a reference crop. The
first spent 39 turns / $2.36 / 664 s and saved nothing; the second timed out
at 900 s with an **empty Blender scene** — every second had gone into
parametric prototyping (`part.py`, `spec.json`, `_proto_test`). Score 0/8
gates, 0/4 fidelity, both runs. The artifact-was-never-saved failure is why
step 2 exists: even the work that was done scored zero because nothing was
on disk when the clock ran out.

The artist's original wording, eevee bowl round
(`projects/eevee-bowl-v2/design/review-log.md`): "Seems we should be
sculpting more for some of these shapes rather than relying only on the
shape generation."

## REJECTED

- Parametric lofts/booleans for organic forms — measured above: two runs,
  ~$5 and 26 minutes total, zero deliverables.
- Polishing before saving — a 900 s timeout converted a full session of work
  into a score of exactly 0.

## SOURCE

`benchmark/results/2026-09-23/ear-baseline.json`,
`ear-candidate-1.json` (runs at 71ed8b3); artist words in
`projects/eevee-bowl-v2/design/review-log.md`. Sibling rule for mixed
designs: [sculpted-part-hybrid-rule.md](sculpted-part-hybrid-rule.md).
