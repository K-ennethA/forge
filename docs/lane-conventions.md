# Lane conventions — read before implementing, cite instead of re-deriving

## The lane contract (orchestration law, 2026-09-19)

A lane is ONE deliverable. Its brief must name four things, and a lane that
cannot state all four is not ready to dispatch:

1. **DELIVERABLE** — one sentence, one artifact class (a rule + its gate; a
   panel; a fix + its pin). If the sentence needs "and" twice, split the lane.
2. **FILES** — an explicit allowlist a diff can be audited against. Everything
   else is read-only; a needed edit outside it is a STOP-AND-REPORT, not a
   judgment call.
3. **EVIDENCE** — the exact suites to run and the numbers the report must
   quote. The lane runs those and stops; the shared gate is the orchestrator's.
4. **STOP conditions** — done, blocked, or grown. "Grown" means the work
   sprouted a second deliverable: report the split, do not absorb it.

Resume rules: a lane is resumed only for (a) a defect found at gate in the
work it just delivered, or (b) exactly one bounded follow-up no larger than
the original. Anything else is a NEW lane briefed from this file plus the
prior lane's report — never from its transcript. Rounds past the second on
one transcript cost more than a fresh start and blur the audit trail.

No waiting turns: a lane whose next step depends on an external event closes
its turn; the event's notification resumes it.

Evidence must fit the deliverable: a verification battery longer than ~15
minutes of wall time is over-specified — prove the mechanism (a hash, an
invariant) rather than repeating runs, and prefer one green run on top of a
structural proof to N green runs in place of one. A long battery that is
genuinely needed runs as ONE background script the lane launches and closes
on, never as agent-held sequential runs.

Orchestrator side: at most two implementation lanes in flight, never two in
one file area; the shared gate launches only when no lane is editing.

One page of the facts every agent lane otherwise rediscovers at full price.
When a brief references this file, these definitions are binding; a lane that
needs to depart from one says so in its report with the measured reason.

## Measurement conventions (settled the expensive way)

- **Hip joint** = the average of the `DEF-thigh` heads. Never the Rigify
  `hips` control's head — it points downward and sits ~306 mm above the
  sockets; a 20° trunk fold swings it forward while the pelvis loads back.
- **Setback** is positive rearward along `−forward_axis`, measured from the
  ankle line, at the hip joint.
- **Sole vertices** = vertices whose dominant weight among **deform groups
  only** is a foot/toe bone of a leg. The autotagger's `tag_*` groups carry
  weight 1.0 and win every vertex if not excluded. The whole-mesh lowest
  vertex is a thigh at rest and a hand at a landing absorb. Open question,
  recorded: the werewolf's heel is `DEF-shin.*.001`-dominant and currently
  outside the sole set (clamp and gate agree; both exclude it).
- **Leg reach** is measured off the rig by pushing the IK target far and
  reading what the solver returns — not summed off a pre-bent rest chain
  (3.5% off) and not hip→ankle/rest-chain ratio (saturates once
  `IK_Stretch = 0`; two dead metrics documented in `rigforge_anim.py`).
- **Extension caps** measure the **deform** chain (`DEF-*`). FK bones sit at
  rest during IK clips and report the rest pose on every frame.
- **`ROTATION_DIFF` folds into [0, π]**; `mathutils` reports the long way
  round. Use `correctives.fold_rotation_diff`. Curves keyed past π are
  unreachable.

## Test infrastructure

- Headless suites: `"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
  --background --factory-startup --python <suite>`; discovered by
  `run_tests.ps1`'s `addon/tests/headless_*.py` glob; print `N checks, M
  failed` and exit 0/1.
- **Ports**: 9876 is the live scene — never touch it from a lane. Suites bind
  9878–9914 (taken as of 2026-09-19); new suites take the next free one and
  say which.
- **The shared synthetic character (`headless_rigik.build_character`) is
  byte-deterministic since 2026-09-19** — the suite gates its own geometry
  digest every run. Assertions still derive bounds from measured geometry as
  good practice. The product's deterministic retopo route is
  `rigforge_retopo` `method="decimate"` (2026-09-19): the fixture asks for it
  by name and `headless_rigforge` gates it with a two-run digest comparison.
  The Quadriflow route stays nondeterministic at normal threading (225
  vertices up to 45.8 mm apart run-to-run; `OMP_NUM_THREADS=1` changes
  nothing, only a whole-process `blender --threads 1` launch stills it, 7/7
  byte-identical — a launch flag no call can set).
- A lane runs its own suite plus the suites covering files it edited — and
  stops there. The orchestrator runs the shared gate once at commit time; do
  not re-run other lanes' suites "in case".

## Local worker gates (learned at full price, 2026-09-19)

The first live dispatch replaced a 6,527-line module with a 7-line stub and
PASSED its gate, because the gate checked only the changed detail (hint text
present, file compiles) — both true of the stub. The dispatch's own rollback
never fires on a green gate. Rules:

- An acceptance gate must verify the WHOLE artifact, not the delta: run the
  module's real test suite (imports alone would have caught the stub), or at
  minimum an integrity check (line count within tolerance of the snapshot,
  key symbols still defined).
- A bespoke content-assertion script is the weakest acceptable gate and only
  for files nothing imports.
- Review the diff stat before keeping a worker pass; a chore-sized brief with
  a thousand-line diff is the stub failure wearing a green light.

## Lane hygiene

- Agents never spawn agents; nothing windowed ever; never pay; no commits —
  the orchestrator gates and commits.
- "Nothing windowed" includes the in-app browser pane (2026-09-24: a
  download agent's navigate calls kept fronting the pane on the artist).
  Agents use WebFetch and command-line fetches; the browser pane is the
  orchestrator's, and only when the artist should look at it.
- Artist placements (design/artist-edits.json journals) are inputs, never
  errors; code must work with them and never "correct" them.
- Every threshold carries its derivation or its experiment; re-pin a moved
  number honestly with the measured value, never tune one to pass.
- `build-plan.json` has exactly one writer (`pipeline.py`); bridges and tools
  read it only.

## Two speeds (2026-09-24, from the artist's speed critique)

The artist's parity bar: forge output must at least MATCH a freehand agent
run of the same task — in quality AND wall time. The process serves that
bar, never the reverse.

- **Draft mode** — any FIRST version of an asset, and all exploration: one
  agent turn. Generate, save with a receipt, render, show the artist.
  No lane ceremony, no gate wall, no benchmark run. Minutes, not hours.
  Drafts are never consumed by the game or a print.
- **Production mode** — only what a consumer actually takes (the game's
  drop-ins, printable exports) gets the reproducible build, the gates, and
  the contract checker. That work earned its rules: every production
  delivery this week landed in one attempt because of them.
- **Commit gates are scoped**: run the suites the change touches; the full
  parallel wall runs on a schedule or before a release wave, not per commit.
- **When in doubt, measure**: the benchmark's sufficiency rule already
  scores time and quality — freehand vs pipeline on the same task is a
  comparison to RUN, not to argue.

## Edits vs rebuilds (2026-09-25, from the artist's speed critique)

Every asset is produced by a reproducible generator script (one headless
run); the .blend is output, never the thing you edit — hand edits are
overwritten by the next build.

- **Parameter tweak** (an ease value, a color, a length, a threshold the
  artist wants nudged): the ORCHESTRATOR edits the constant in the build
  script directly and reruns the build — minutes, no lane, no report
  ceremony. Renders to the artist, done.
- **Structural change** (new geometry algorithm, new gate, new rig
  behavior, anything touching more than declared constants): a lane, with
  gates and a report.
- Lane briefs must declare their tunable constants NEAR THE TOP of the
  build script with the artist-facing name in a comment, so the next
  parameter tweak is a one-line find. A build whose knobs are buried is a
  defect.

## Quality tier is a per-asset knob (2026-09-24, artist)

The tier is DETERMINED per asset by its role and platform budget, not fixed
globally: a hero character earns the semi-realistic tier (30–50k tris,
baked normal/AO detail from a high-poly source, real texturing); a crowd
NPC, background prop, or mobile-budget asset may correctly be low-poly —
"low poly for a game where it doesn't matter, or something more realistic."

The pipeline serves both from ONE high-detail master per asset: build/
generate high, then bake down to the requested budget (this is also how
LODs fall out for free). The tier is declared in the asset's task config
and the delivery is gated against the declared budget, not a universal one.
What was actually below the bar on the first protagonist: an asset in a
HERO role delivered at crowd quality — a mismatch of tier to role, which
this knob exists to prevent.
