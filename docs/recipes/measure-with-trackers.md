# Measure with trackers, move with grab_to, render only for appearance

## WHEN

Any spatial adjustment task: putting a part, a prop, a bone or a landmark at a
position, lining two things up, closing or opening a gap, keeping something
on a floor or an axis while moving it along another. It applies whenever the
question is "is it in the right place", and not "does it look right".

## THE RECIPE

1. **Track the landmarks first.** `track_points(object, points=[{name,
   location_mm | vertex_index | bone}])` on every point the task depends on:
   the contact corner, the peg tip vertex, the hand bone. A vertex tracker
   reads the *evaluated* mesh, so it follows armature deformation. A location
   tracker is held in the object's frame and moves with the object. A bone
   tracker reads the posed head (or `end: "tail"`).
2. **Read the start state by number.** `probe(names, pairs=[[a, b]])` gives
   world positions and distances in mm. It writes nothing and costs 17–21 ms per
   socket round trip (4 trackers, 2 of them deformed vertices; measured on two
   runs), so call it on every step.
3. **Grab by numbers.** `grab_to(object, target_mm | delta_mm, axis_locks,
   pivot="origin" | tracker, bone=None)` solves the translation so the pivot
   lands on the target, with the locked world axes held. If the locks make the
   target unreachable, it refuses with the shortfall and moves nothing. Hold
   the thing that must not move (a floor contact, a height) with a lock rather
   than trying to put it back afterwards.
4. **Verify by probe.** The grab report already carries the residual and a
   readout of every tracker on the object (and, for an armature, on the meshes
   it deforms). Use `probe` on anything else that should not have moved.
5. **Render at most once, at the end**, to judge appearance (shading,
   silhouette, whether it *reads* right). A render never counts as evidence of
   position.

## WHY

`headless_trackforge.py` (Blender 5.0.1, port 9919, 115 checks):

- `grab_to` lands the pivot within **1.907e-06 mm** of the target (worst of
  the eight axis-lock combinations). That is the float32 floor of a location
  near 50 mm: half an ulp of 0.05 m. Locked location channels stay
  bit-identical because the exact path never writes them.
- A pose bone steered so that a **deformed skin vertex** lands on a target
  gets there with a **2.572e-06 mm** residual after 2 Newton steps. An independent pose-matrix
  recomputation agrees.
- A vertex tracker follows a 77.4 mm pose-driven displacement and stays within
  **4.26e-06 mm** of the pose-matrix ground truth.
- A refused grab reports the exact shortfall, e.g. `shortfall (0, 0,
  5.000000) mm` with Z locked. It leaves the matrix, the pose channels and the
  deformed mesh bit-identical, including after the probed path's finite-step
  measurement.

The eevee-bowl-v2 seating rounds ended each time on a render that was "looked
at, and disagreed with" (`seating.md`, REJECTED). The eye is a millimetre-class
instrument at best, and every look costs a render.

## REJECTED

- **Render-inspect-nudge loops.** They are slow (a render per step) and
  imprecise (pixels at the render's zoom are the resolution). They also do not
  converge: each nudge is a guess about a number the scene already knows. This
  toolkit exists because of them.
- **Setting `location` by hand arithmetic from a formula.** This fails for
  deformed pivots, parented objects and pose bones, where a world move is not a
  channel move. `grab_to` measures the response (`solve.pivot_response`) and
  refines on the measured residual instead.
- **Moving, then restoring a coordinate that should not have changed.** Use an
  axis lock. The refusal tells you before anything moves that the target needs
  the locked axis.

## SOURCE

- `addon/forge/tools/trackforge.py`: module docstring (the solve, the exact
  and probed paths, the float32 precision floor) and the `REACH_TOLERANCE_MM`
  / `CONVERGED_MM` / `ACCEPT_MM` / `PROBE_STEP` derivations.
- `addon/tests/headless_trackforge.py`: the fixture and every number above.
- `docs/recipes/seating.md`: the render-and-disagree failure this replaces.
