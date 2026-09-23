# Artist review log — eevee-bowl-v2

Append-only. Each entry ties a render the artist reviewed to their verdict, in
their own words — these are the labels the render-judgment corpus harvests.

## 2026-09-21 — renders/assembly-v2.png (first sculpted assembly)
> the ears don't have the outline like in the picture, also the fur pattern
> around is shapes instead of something that should be sculpted more closely
> like our 3d model we did for the character. the ear placement doesnt really
> line up with the picture either. the tail isn't thick like the picture
> either and why do we have small triangle cutouts at the bottom.

Resolution state: cutouts diagnosed (arcade arches) and removed; tail
resculpted plump; fur resculpted as tufts; ear inset added; placement
re-measured — but see the later entries.

## 2026-09-21 — renders/assembly-v2.png (post-fillet round)
> right away I can see the ears aren't sculpted and are on the wrong side of
> the bowl (close to the tail instead of the opposite side), the tufts aren't
> as crisp as they should be, they should overlap each other like the picture
> [+ artist-annotated screenshot: a stray component at the collar/base seam,
> red underline was the annotation, not a render artifact]

Resolution state: open at pause — ear side correction (front axis, not back),
stale-generation sweep incl. the stray fragment, tuft crispness comparison.

## 2026-09-21 — renders/ear-closeup-v3.png + assembly-v3.png (paused round)
> the ears have the correct indentation but no volume to them and are angled
> the same direction instead of towards us

Reading, for the resume contract: (a) the INSET is now right; (b) the ear
SHELLS lack volume — flat slabs with an inset rather than rounded backs; the
sculpt needs true shell depth (thickness/curvature), not another inset pass;
(c) the two ears carry the SAME rotation — placement must MIRROR the yaw per
side (+theta / -theta toward the front) so both inner faces turn toward the
viewer like the sheet's front view, instead of copying one transform to both.

## 2026-09-23 — benchmark ears (front/three-quarter/top renders)
> ears are more curved like this [+ Eevee character sheet:
> refs/eevee-sheet-artist-2026-09-23.png — wide base, curved leaf shape,
> pointed tip]

Context: the benchmark run fixed mirror (0.5 deg) and volume (0.397) but the
silhouette rendered as a tall narrow blade. Reading: shape fidelity to the
reference outline is its own requirement — a silhouette-match metric,
extracted deterministically from the sheet, joins the ear gates.

## 2026-09-23 — ear v2 final renders (post root-rebuild)
> seems we lost the indentation and the ears really don't have volume, like
> there should be a small curve in the back as well like a real ear is
> thicker at the base

Reading: (a) the cupped/recessed INNER face regressed out during the
rebuilds — it is a standing requirement, not optional; (b) volume means a
THICKNESS PROFILE, not the volume_ratio number (0.537 passed while the shell
read thin): thick base, rounded convex back, tapering toward the tip like a
real ear. Note: a genuinely thick base also dissolves the constructional
min_wall failure at the root — the artist's requirement and the failing gate
are the same fix. volume_ratio alone cannot see this; a thickness-profile
metric (base vs tip) is the candidate gate.
