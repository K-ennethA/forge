# Elias - unit state (orchestrator-maintained; briefs point here instead of restating history)

- **Current:** v1 DRAFT (uncommitted), static build + rig, no clips. Spec: review-log 2026-10-02 "NEW UNIT: PROFESSOR ELIAS".
  Runner: improve/elias_run.ps1 (~85 s: build ~80 s + parallel renders / face probe / contract check + compose).
- **LOCKED:** nothing yet (awaiting the artist's first verdict on renders/elias/elias_v1_sheet.png).
- **Pipeline:** wren stages copied + adapted (s1 verbatim; s5 = Wren ribbon locks + beard / mustache locks + beard core;
  s6 hair proxy split scalp / beard; s7 rig + staff / book bones + grip records + staff roll search). Wren files untouched.
- **Key knobs (top of elias_build.py):** MACRO / TARGETS (age 0.80, head-scale dials), BROW_W, FACE_LINES, BEARD_ZONE /
  BEARD_LOCKS / BEARD_LEN / BEARD_POINT / BEARD_CORE_*, MUSTACHE*, HAIR_LOCKS / HAIR_KIND_W, GLASSES, MANTLE_* / CAPELET /
  CREST, SATCHEL, STAFF / STAFF_REST_OFF, BOOK / BOOK_HOLD, CRAVAT, palette palettes/elias/default.json.
- **Numbers (v1):** 45.7k tris (hero 30-50k); 5.96 heads (house rule, Wren 5.94); mouth v_ratio 0.274, w/eye 0.687;
  iris 56.6 %; staff roll 240 deg / wrist bend 43.8 deg (Wren idle 41.4).
- **Known residuals:** face probe under-eye far above Wren (flatness 5.5 mm; 1.3-2.4 mm with the painted age lines off --
  the lines are holes to the probe's skin set, the rest is the age macro's lids); robe shows MPFB torso relief (navel);
  hair top reads cap-like / thin; beard ribbons flat + ragged tips; cravat now fully hidden by the beard; fingers pass
  through the tome (no finger pose); contract cell_fit fails on the 1.86 m staff (report-only policy); the checker
  crashes on a clip-less rig (run on improved/ instead).
- **Open artist questions:** skin tone (sheet not sampled); eye colour; beard length / shape (point vs full spade, show
  the cravat?); mustache droop; glasses lens (open rims vs glass tint) + frame colour; staff scale (1.86 m) and hold;
  where the tome rests (hand / belt / satchel); hair volume on top (receding vs fuller); age lines yes/no; movement intent
  (how he walks / casts) before any clip.
