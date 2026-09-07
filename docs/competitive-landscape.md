# Competitive landscape — verdicts and standing strategy (2026-09-07)

Source: deep sweep (200 searches, ~150 primary fetches) across the four domains: 3D printing help, mold making, animation/game, robotics/mechanics/toys. Full detail in the research transcript; this file keeps what changes our behavior.

## The strategic read (standing policy)

**One pipeline, four exit validators — never four products.** The generative front-end, parametric kernel, and agent loop are identical across domains; only the thin rule-based validator differs. (Meshy's $1.5B story is one engine + ONE added validator.)

**Sequence: lead with 3D printing + mold making together.** Molds are an EMPTY room (verified across 20+ companies: zero flexible-mold undercut analysis, zero non-planar parting, zero vent reasoning, zero generated-input molds). Toys/mechanisms next (part-separation problems). **Game assets ship as free fall-out of the pipeline, never as the pitch** — four ~$1B-funded companies fight there, and community sentiment (52% net-negative on genAI, mandatory AI labels on every storefront) is a headwind our parametric/provenance story sidesteps.

**The moat is the rule set and curated primitives, not the agent.** Every independent benchmark agrees: LLMs can't create correct structure but select/parameterize/edit near-perfectly against a domain SDK with typed assertions and deterministic checkers (Articraft's last-to-first flip; CADSmith's 38× error reduction; BenchCAD 0.865 edit vs 0.397 generate). Our moat candidates, none existing anywhere at any price:
- flexible-mold demoldability grade (shore hardness × undercut depth × wall thickness)
- non-planar parting surfaces (Cardiff algorithm: published 2018, unimplemented by anyone)
- vent placement from air-trap geometry
- image → figure → mold end-to-end
- snap-fit geometry + strain/safety-factor in one tool (calculators emit no geometry; generators compute no strain)
- print-in-place articulated figures with printer-calibrated clearances (open field: one 3★ repo)
- a machine-readable print tolerance/clearance database (none exists)
- semantic component records (mount points, plunger axes, keepouts — only GPL NopSCADlib has the pattern)
- local inference as the product (every commercial rival is cloud-metered; our generation COGS is zero)

## The uncomfortable mirror

**`earthtojake/text-to-cad`** (MIT, 14.6k★ in 5 months, one author): build123d + Claude agent skills, DfAM checks, slicer/Bambu orchestration, URDF, and the **step.parts API — 16,847 free STEP components incl. switches/servos/battery holders**. It is our most direct competitor AND a resource. Policy: **use or match, never rebuild** — evaluate step.parts as a geometry source for maker_lib envelopes (their records lack semantics — no mount points/travel axes — which is exactly our differentiator #8 layered on top). Our agent architecture itself is NOT a moat (Zoo, Backflip, PTC shipped the same loop in 2026).

## Patterns to steal (fold into the verifier/agent builds)

1. **Pairwise tournament verification** (O(log b) VLM calls), never absolute 1-10 scores.
2. **Hypothesis reversion** — always re-inject the previous best as a candidate; its ablation diverges (this is the fix for context-rot failures).
3. **Visual imagination** — generate a target image from the text intent FIRST, judge renders against it. Cheapest high-value upgrade.
4. **Tweak/leap alternation** at moderate breadth×depth (4×8 ≻ 32×1 at equal cost).
5. **Credibility tiering** on every claim: `finding < prediction < proxy < executed-solver` — stamp mold/mechanism outputs with it verbatim.
6. **Round-trip certificates**: re-derive joint graphs from articulated meshes, gear ratios from teeth, netlists from wiring — compare derived-vs-intended, not similarity.
7. **RAG the API + an anti-patterns corpus** (compilation 40.8%→70.0% from 500 examples, no fine-tuning).
8. **Tool tiering/intent filtering** (69→15 tools ≈ 77% token savings) — audit our 63-tool schema cost.
9. **Checkpoint + fresh session at milestones** (context accumulation is the measured failure mechanism).
10. Mold UX: **propose-then-drag** ("automatic first pass, then the human drags the seam"), **grade the plane** (demoldability heatmap), **surface cost before the pour** (we already quote silicone ml — keep), **named archetypes** not a generic button.
11. Wiring IR: **Wokwi's diagram.json** (~80-part vocabulary, free executable oracle) if/when wiring goes visual.

## Dead ends (never do)

Train models (Unity Muse died; CSM acquihired despite broadest scope). Custom DSLs (JITX fled its own to Python). Ship a bridge as a product (thousands of identical MCP forks). Fire-and-forget codegen (BlenderGPT 5.6 vs verified 88.9). Rigid-mold draft rules on silicone. Donation pricing (Fritzing: $78 lifetime). Depend on free hosted services from big vendors (Mixamo).

## Validated by the sweep

Process-boundary GPL architecture (ours, independently converged on); rules-not-learned manufacturability (universal among the credible); parametric-first trust story ("everybody demands an editable feature tree before accepting an auto-generated bracket" — our build123d file is the deliverable AND the anti-slop answer); local-first (nobody commercial ships it; users want it); pricing precedent for solo dev: Plasticity's perpetual $149/$299, Sloyd's unlimited-parametric + credits-for-GPU shape, Meshcast's $9/$20 + lifetime proving mold willingness-to-pay.
