# datasets/ — training-data harvest

Four JSONL corpora pulled from work this repo already did and already
verified, for fine-tuning the local tier (qwen2.5-coder for chores,
qwen2.5-VL as a render judge) on Forge's own labels instead of generic
data. Everything is local; nothing here is or should be uploaded
(NEVER-PAY law).

Regenerate any time with:

```
"C:\Users\kenne\AppData\Local\Python\pythoncore-3.14-64\python.exe" datasets\harvest.py
```

(any Python 3.10+ works — stdlib only, no venv/install needed). It prints
a record count per corpus and rewrites all four `datasets/*.jsonl` files
in place. Re-running is always safe: it replaces its own output, reads
sources read-only, and never touches git history, `worker/runs/`, or
`projects/`. See `datasets/SCHEMA.md` for exact record shapes and every
truncation cap.

## What each corpus is for, and current size (this harvest)

| Corpus | Records | Use |
|---|---|---|
| `code_pairs.jsonl` | 128 | coder LoRA — (commit message, diff) pairs, the bulk of the training signal |
| `worker_runs.jsonl` | 9 | coder LoRA — the richest label (brief + real diff + real gate verdict), but still small |
| `recipes.jsonl` | 20 (10 accepted + 10 rejected, from `docs/recipes/`) | recipe retrieval / VLM-judge context — numeric thresholds with their derivations, plus contrastive rejected-alternative pairs |
| `render_judgments.jsonl` | 26 (1 labeled) | VLM judge — render/reference pairs; almost entirely unlabeled today |

## What's thin

- **`worker_runs.jsonl` is small (9 runs)** because the local-worker
  dispatch protocol is new. It is also the *best-shaped* corpus for the
  coder tier once there's more of it — every record already carries a
  verified PASS/FAIL, not an assumption. This grows automatically as
  `worker/runs/` gets more dispatches; no harvest change needed.
- **`render_judgments.jsonl` is 25/26 unlabeled.** Renders exist for three
  projects, but only one sentence in one design log (`eevee-bowl-v2`'s
  `organic-rework-log.md`, about `assembly-v2.png`) ties a specific render
  to a specific verdict in words. Everything else is inventory only —
  paired with references, but nothing says pass/fail/why.
- **Only `eevee-bowl-v2` has a `design/refs-manifest.json`.** `werewolf`
  and `litwick-lamp` renders fall back to a plain listing of `design/refs/`
  (tagged `reference_source: "refs_dir_listing"` so it's never confused
  with an authored pairing) — `litwick-lamp` has renders but no
  `design/refs/` at all, so its one render has `reference_source: "none"`.
- **`docs/recipes/` exists (10 files) and is the primary source for
  `recipes.jsonl`** — it appeared mid-harvest (another lane's deliverable,
  landing concurrently in this same working tree) and already carries the
  native WHEN/THE RECIPE/WHY/REJECTED/SOURCE structure the brief asked
  for, so `harvest.py` reads it directly instead of re-deriving sections
  from project logs. All 10 files happened to have a non-empty `REJECTED`
  section, which is why the corpus is an even 10 accepted / 10 rejected.
  The project-logs fallback path (`build-plan.json` stages + design
  `*.md` logs) still exists in `harvest.py` and would activate
  automatically if `docs/recipes/` were ever empty or missing again — it
  is currently unused but exercised by this harvest's own history (it
  produced the corpus's first version, before `docs/recipes/` landed).
- **`docs/recipes/` only covers what's been written up by hand so far**
  (10 topics: peg/boss junctions, seating, cross-part bridges, min-wall
  triage, fixture freezing, deterministic image extraction, sculpt
  thickening, the sculpted-part hybrid rule, sole-lock foot weights,
  measuring reach off the rig). It does not yet cover every recipe implicit
  in the project design logs (e.g. `eevee-bowl-v2/check-log.md`'s tail-peg
  derivation) — those stay in the fallback path only, so they are not in
  the harvested corpus while `docs/recipes/` is non-empty. If that content
  is valuable to train on, either write it up as a `docs/recipes/` entry
  (preferred — same author intent as the rest of that directory) or change
  `harvest.py` to merge both sources instead of choosing one.

## What future runs should start recording, to enrich this

1. **A one-line verdict sentence beside every render the bridge saves.**
   Right now a render lands in `projects/*/renders/` with no record of
   what anyone thought of it unless someone happens to write a paragraph
   in a design log that happens to name the file (this harvest found
   exactly one such sentence). If the bridge that saves a render also
   appended one line — file name, reference used, PASS/FAIL/WARN, why —
   to a per-project `design/render-log.md` (or a small JSONL the bridge
   owns), `render_judgments.jsonl` would go from 1 labeled record to
   effectively every render, which is what a VLM-judge LoRA actually
   needs in volume.
2. **A `design/refs-manifest.json` for every project that has renders**,
   not just `eevee-bowl-v2`. `werewolf` and `litwick-lamp` both have
   reference images sitting in `design/refs/` with no manifest tying a
   specific reference to what it's a reference *for* — the directory
   listing fallback this harvest uses is strictly worse than an authored
   manifest (no `tag`/`note`, no way to know which ref matches which
   render).
3. **More worker dispatches.** `worker_runs.jsonl` is the single richest
   record shape here (brief + diff + real gate verdict, no inference
   needed) and there are only 9 of them. Nothing to change in the
   harvester — this is purely a volume gap that closes with normal use
   of `worker/runs/`.
4. **Suite verdicts recoverable from commit messages, structurally.**
   `code_pairs.jsonl` currently defaults every commit's `verdict` to
   `"PASS"` (true per the gate-before-commit convention) with a
   `"ROLLBACK"` heuristic on `revert`/`rollback` in the subject. Some
   commit bodies already state an explicit stage-level result in words
   (e.g. "reached CHECK GREEN" / "check RED, recorded" in
   `ce41a88`) — if that became a consistent, parseable convention (a
   fixed marker string rather than prose), `code_pairs.jsonl` could carry
   a finer verdict than the current binary PASS/ROLLBACK.
