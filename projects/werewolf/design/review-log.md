# Artist review log — werewolf / game characters

Append-only. Artist verdicts in their own words; these drive gates.

## 2026-09-24 — protagonist_human vertex-color look (look-dev renders)
> we still never fixed the back, also the clothes blends with the skin in
> multiple parts, its good enough to not block the other agent from
> developing but we need to fix these things

Reading: (a) the BACK geometry defect stands — the torso's back carries
open-jacket front geometry (also flagged by the look lane); it predates the
color pass and is a mesh repair, not a paint fix; (b) the vertex-color
REGION boundaries bleed — skin color appears inside clothing zones (and/or
vice versa) in multiple places; region assignment + the 3-ring blur need
tightening, and region purity should become a measured gate (color-class
audit per tagged zone, allowed mixing only within N rings of a boundary);
(c) shipping verdict: current asset is good enough for the game side to keep
developing — fixes land as a rebuild, no contract change.

Both defects go to the UV/geometry repair + look-fix lane (queued behind the
locomotion wave lane — same file area, so they cannot run concurrently).
