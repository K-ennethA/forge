# Deterministic image extraction (the sprite/floorplan law)

## WHEN

The artist hands over a picture that has to become geometry with real,
correct coordinates — a floor-plan sketch is the documented case, and the
same law applies to any drawing whose measurements matter.

## THE RECIPE

1. **If they gave you a picture, you must run the deterministic extractor on
   it** (`floorplan_extract` for a floor plan). Reading the drawing with your
   eyes and typing out coordinates is **forbidden** — not discouraged,
   forbidden.
2. Run extraction first with **no scale** (`mm_per_px` omitted). It returns
   features in reading order, axis-aligned walls, per-region crop boxes, and
   exactly **one** calibration question.
3. **Geometry comes out of the tool; only the names/labels come from you** —
   read the crops for words, match them against a lookup table. That is the
   whole division of labour.
4. Ask exactly the **one** calibration question the report gives you,
   quoting a pixel length from it. Never scale the resulting numbers by
   hand — re-run the extractor with the resolved `mm_per_px` so every
   coordinate stays measured, not multiplied.
5. If a colour-key read was assumed (which colour means which feature), say
   which assumptions were made.
6. Build geometry **only** through the deterministic builder
   (`floorplan_build`) — never ad-hoc scripted boxes, even as a stopgap.
7. If the extractor refuses the picture, fall back to describing it in words
   (ask for the one real dimension the artist already knows) — never fall
   back to guessing at the image.

## WHY

`assistant/system_prompt.md`, quoted directly: *"Reading a drawing with your
eyes and typing out coordinates is **forbidden**, and it is forbidden because
it was tried: the eyeballed plan built the wrong footprint, put the rooms in
the wrong places, and added a diagonal wall that exists nowhere in the
drawing. A tool that classifies the colours, walks the pixel boundary and
snaps to the drawing's own grid cannot make any of those mistakes — a
diagonal is impossible in its output by construction."*

On why geometry must come only from the deterministic builder: *"Ad-hoc
scripted boxes cannot be diffed, cannot be kept-or-clobbered by id, and
pollute the scene: one stale improvised object made a straight-walled level
look like it had a diagonal in the owner's first test."*

## REJECTED

- **Eyeballing the image and hand-typing coordinates.** Measured failure:
  wrong footprint, rooms in the wrong places, a diagonal wall invented that
  does not exist in the source drawing.
- **Ad-hoc scripted geometry** in place of the deterministic builder. One
  stale improvised object made an otherwise straight-walled level look
  diagonal on the owner's first test.
- **Redrawing the echo-back diagram from the picture again**, instead of
  from the extracted plan file. This "would re-introduce exactly the error
  the extractor exists to remove — and the SVG would then agree with the
  drawing while the level disagreed with both."
- **Scaling extracted numbers by hand** after getting the calibration answer,
  instead of re-running the extractor with the resolved scale.

## SOURCE

- `assistant/system_prompt.md` — "## Floor plans" section, step 0 ("IF THEY
  GAVE YOU A PICTURE...") and step 3 ("Every number in that SVG comes out of
  the plan file, not off the picture").
