# Forge Automation Thesis — build vs buy, decided by evidence

Date: 2026-09-06. Source: deep web research (primary sources; full citations in the research report). Standing policy until revised. The owner's challenge that prompted it: "are we choosing what's easiest/available instead of building what fits?"

## The verdict in one paragraph

The critique was right about the habit, wrong about the remedy. Generative 3D models are the fastest-improving, cheapest-to-swap layer — training our own is the worst possible investment (frontier models take 64+ datacenter GPUs; even the best-funded pure-play company, Kaedim, still human-verifies 100% of outputs after 3 years and $15M). The slowest-improving, most-neglected, and most-buildable-on-one-GPU layer is **verification, constraints, and orchestration** — exactly the layer Forge already leads on (the most popular open agentic-Blender tool has NO visual feedback loop at all; we're ahead of tooling, behind papers). Build there. Adopt two specific better open components. Keep swappable seams everywhere else.

## Corrections to our current architecture (evidence contradicts what we ship)

1. **Visual self-critique must not be the sole judge** (highest confidence). 123k human votes show render-based judging systematically rewards visual impact over downstream utility (+144 ELO for textured over untextured; the SAME model scores 78 ELO higher as a splat than a mesh). Our render-and-look loop inherits that bias. FIX: a **geometric verifier** that runs beside the eye — manifoldness, self-intersections, thin-feature map, poly budget, UV distortion, symmetry residual, silhouette IoU vs reference, edge-loop density near joints — as a scored report the agent must satisfy IN ADDITION to the look. (~1–2 weeks; much of it exists in mesh_diagnose/checks — unify + extend + make it a gate.)
2. **Quadriflow is no longer the retopo frontier** (high confidence). Learned artist-mesh retopo ships commercially (Hunyuan3D-PolyGen, API-only) and its open substrate **BPT** runs on our 12 GB card (fp16, ~2 min/mesh, point-cloud-conditioned = usable as a retopo pass). FIX: add BPT as a retopo backend (license check first — unverified), keep Quadriflow fallback, watch Meshy T2 (3-second retopo, weights "preparing for release"). Caveat flagged by research: no practitioner testimony on deformation-zone quality obtained — verify on our own rigs before defaulting.
3. **OrcaSlicer demoted to G-code generator only** (high confidence): CLI --orient is broken (can't reliably disable auto-orient headlessly — it may silently override computed orientations), auto-orient documented choosing a floating cantilever over a bridge, Fix Model is Windows-only, no hollowing/wall enforcement. We mostly already hand it solved meshes; make that a hard rule + pin the version.
4. **TRELLIS.2 official repo wants 24 GB** — our ComfyUI int8 deployment (8.15 GB peak, proven) is not just a convenience, it's the only reason it runs locally. Keep the Comfy path; never migrate to the official pipeline (also nvdiffrast-licensed).

## Validated by the evidence (keep, do more)

- **The parametric half is the strategic bet, not the fallback.** Sloyd — the one vendor credibly claiming game-ready output — built a parametric kernel + templates and uses AI only to select/parameterize. Research agrees: agent-authored parametric programs lose on raw fidelity but win decisively on semantic adherence, editability, reliability (100% execution success, ~86% human preference). Push MORE asset classes parametric; generative 3D is for the organic residue.
- **MIT-clean model choices** (Hunyuan's license is a landmine for games: EU/UK/KR exclusions, MAU caps, mandatory AI-content labeling; we avoided it).
- **The human accept/reject gate is irreducible (2026)** — so buddy/check-my-work/keep-or-scrap is the correct product shape, not a stopgap. Also irreducible: art direction/detail placement, deformation intent, control-rig authoring, functional print judgment, taste.

## The build roadmap (ordered)

1. **Geometric verifier gate** (1–2 wk) — correction #1. Grounding matters: LLM-over-precomputed-features scored 90% vs 37.5% for pure prompting in printability assistance.
2. **BPT retopo backend** (~1 wk integration + license verification) — correction #2.
3. **Deterministic print-prep solver** (3–5 wk, GPU-free): repair (manifold3d/CGAL alpha-wrap) → orientation (reimplement Tweaker-3 scoring — GPL, reimplement don't link — + bridge-vs-cantilever terms) → thin-feature enforcement → **BSP part-splitting with booleaned connectors** (academic lineage strong and abandoned since ~2017; nobody ships it; our segmenting is the closest thing alive). LLM judges only over the solver's report.
4. **Skill library + retrieval for the agent** (2–3 wk): retrieval over Blender/build123d API docs (−26% execution errors, 5× complex ops in LL3M) + compiling successful scripts into a reusable library (SceneCraft) — our flows are the seed of this; extend to authored-part templates.
5. **2D-LoRA house style** (research next): fine-tune an image model on the owner's style (12 GB-feasible), feed stylized images to the swappable image-to-3D seam. Cheapest route to "our look"; zero 3D training.

## Standing rules distilled

- Never train a 3D generative model; keep every model behind an adapter seam and swap quarterly as the field moves (TRELLIS→TRELLIS.2 took one year; part-based gen went 0→shipping in 12 months).
- Deterministic geometry before LLM judgment, always; the LLM reasons over computed features, never raw meshes.
- Renders judge beauty; geometry judges truth; both gates must pass.
- Parametric first; generative for the organic residue; the human is the taste gate by design.
