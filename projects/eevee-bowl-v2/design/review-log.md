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
