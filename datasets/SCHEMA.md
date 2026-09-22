# Dataset schema

Training-data harvest of the repo's own verified work, for fine-tuning the
local tier (qwen2.5-coder for chores, qwen2.5-VL as a render judge) on
Forge's own labels. Everything here is derived from files already in the
repo — no new labeling, no uploads (NEVER-PAY law: all local).

Regenerate with `datasets/harvest.py` (stdlib-only). It reads sources
read-only and rewrites `datasets/*.jsonl` in full each run — re-harvesting
replaces its own outputs, never edits sources, and is safe to re-run any
time the repo grows.

All four corpora share a top-level shape: `id`, then corpus-specific
content fields, then `verdict`, then `meta`. Every record's text fields are
plain strings (no embedded objects) so each line is directly usable as an
(instruction, output, verdict) training triple, except `recipes.jsonl`
whose `recipe` field is a small JSON object (a recipe's numbers are the
point, not prose).

Truncation is applied to a few large fields and always documented in that
record's `meta` with a `_truncated: bool` flag and a `_chars_full: int`
giving the untruncated size, so nothing is silently cut without saying so.
Caps (chosen to keep single JSONL lines well inside typical LoRA context
windows while keeping small diffs intact):

- unified diff text (`code_pairs.output`, `worker_runs` attempt diffs):
  **20,000 chars** for a commit diff, **12,000 chars** for a worker-attempt
  diff (worker attempts are chore-scoped and rarely approach this).
- gate/worker log text (`worker_runs` attempt gate logs): **4,000 chars**.
- design-log section body (`recipes.recipe` when `source_type` is
  `design_log`, the project-logs fallback): **3,000 chars**.
- `docs/recipes/*.md` section body (`recipes.recipe` when `source_type`
  is `docs_recipe`/`docs_recipe_rejected`, the primary path): **4,000
  chars** (the largest recipe file today is ~5,000 chars total, so a
  single section rarely nears this).
- matched verdict paragraph (`render_judgments.verdict_text`): **1,500
  chars**.

## 1. `code_pairs.jsonl` — one record per git commit

Every commit in this repo's history is gated before it lands (see
`docs/lane-conventions.md`: "the orchestrator gates and commits"), so the
whole log is (instruction, output, verdict) already — this corpus just
extracts it.

```
{
  "id": "code_pairs:<12-char short sha>",
  "instruction": "<commit subject>\n\n<commit body>",   # verbatim from git log
  "output": "<unified diff of the commit, git show --no-color -U3, capped>",
  "verdict": "PASS" | "ROLLBACK",
  "meta": {
    "sha": "<full sha>",
    "short_sha": "<12 chars>",
    "author": "<name>",
    "date": "<ISO 8601, author date>",
    "files_changed": ["<path>", ...],
    "insertions": <int>, "deletions": <int>,
    "diff_chars_full": <int>, "diff_truncated": <bool>,
    "verdict_note": "implicit — every commit on this branch is gated before
        commit per docs/lane-conventions.md; ROLLBACK is a heuristic hit on
        the commit subject containing 'revert' or 'rollback', not a parsed
        gate log"
  }
}
```

Coder-LoRA use: (instruction=brief-like commit message, output=diff) pairs
teach the "given this ask, produce this patch" shape directly. This is the
largest and most uniform corpus.

## 2. `worker_runs.jsonl` — one record per local-worker dispatch run

Reads `worker/runs/<id>/` (skipping `*.stale-*` snapshot directories, which
are empty rollback artifacts, not runs). This is the richest label: a
worker run carries the brief it was given, every attempt's diff, the
gate's own stdout, and a real PASS/FAIL/escalate verdict — the dispatch
protocol's own bookkeeping, not something inferred after the fact.

```
{
  "id": "worker_runs:<run id>",
  "instruction": "<brief.md verbatim — the task as given to the worker>",
  "output": "<attempts joined; see below>",
  "verdict": "PASS" | "FAILED" | "GATE_ALREADY_GREEN" | "UNKNOWN",
  "meta": {
    "run_id": "<id>",
    "backend": "<e.g. aider>" | null,
    "model": "<e.g. qwen2.5-coder:14b>" | null,
    "attempts_used": <int>, "max_attempts": <int> | null,
    "files_scope": ["<path>", ...],
    "acceptance_cmd": "<the gate command run>",
    "escalate": <bool>, "escalation_note": "<text>" | "",
    "attempts": [
      {
        "attempt": <int>,
        "passed": <bool>,
        "changed_files": ["<path>", ...],
        "diff_chars_full": <int>, "diff_truncated": <bool>,
        "diff": "<attempt's changes.diff, capped>",
        "gate_log_chars_full": <int>, "gate_log_truncated": <bool>,
        "gate_log": "<attempt's gate.log, capped>"
      }, ...
    ]
  }
}
```

`output` is the attempts concatenated in order, each headed by
`=== attempt <n> (passed=<bool>) ===`, its diff, then `--- gate ---` and its
gate log — so the record alone shows the worker's own iteration (a failed
attempt's gate complaint, then what the next attempt changed in response)
without needing to join against `meta`.

A run with `attempts_used: 0` (status `GATE_ALREADY_GREEN`) has no
attempts to show; `output` is empty and `verdict_note`-equivalent context
lives in `escalation_note` — these runs are still useful as *negative*
examples (a gate that doesn't test the task) for anything training the
gate-authoring side rather than the coder.

Coder-LoRA use: this is the highest-signal corpus for the chore-tier model
— brief in, real diff out, with a verified pass/fail instead of an
assumption. Still small (9 runs as of this harvest); grows with every
future dispatch.

## 3. `recipes.jsonl` — WHEN / RECIPE / WHY records

**Primary source: `docs/recipes/*.md`.** It exists (as of this harvest,
10 recipe files plus `INDEX.md`, which is skipped — an index, not a
recipe) and each file already carries the native structure this corpus
wants: `## WHEN`, `## THE RECIPE`, `## WHY`, `## REJECTED`, `## SOURCE`
under an `# <title>` heading. `harvest.py` parses these sections directly
rather than re-deriving them. Each file produces **up to two** records:

```
{
  "id": "recipes:docs:<file stem>:recipe",
  "when": "<WHEN section text>",
  "recipe": "<THE RECIPE section text, capped>",
  "why": "<WHY section text>",
  "verdict": "accepted",
  "meta": {
    "source_type": "docs_recipe", "project": null,
    "title": "<H1 title>", "source_file": "docs/recipes/<file>.md",
    "source_citations": "<SOURCE section text, verbatim>",
    "recipe_chars_full": <int>, "recipe_truncated": <bool>
  }
}
```

and, only when the file has a non-empty `## REJECTED` section (every file
did, as of this harvest):

```
{
  "id": "recipes:docs:<file stem>:rejected",
  "when": "<same WHEN section text as the :recipe record>",
  "recipe": "<REJECTED section text, capped>",
  "why": "rejected alternative(s) — tried and abandoned; see the sibling ':recipe' record (same 'when') for what was used instead",
  "verdict": "rejected",
  "meta": { "source_type": "docs_recipe_rejected", ... same shape as above }
}
```

The `:recipe`/`:rejected` pair sharing one `when` is deliberate: it gives
a recipe-retrieval or judge model a same-situation (accepted approach,
rejected approach) contrast pair — what to do *and* what was tried and
didn't work, for the same trigger condition — rather than only the
positive example.

**Fallback source, used only if `docs/recipes/` doesn't exist or holds no
`.md` files**: reads two things directly per `projects/<name>/` instead —
`design/build-plan.json` stages (`source_type: "build_plan_stage"`, one
record per stage that has left `pending`; WHY = the stage's `does` field,
RECIPE = its `numbers` + `artifacts`) and `design/*.md` logs other than
`requirements.md` (`source_type: "design_log"`, split on `##` headings,
one record per section; WHY = the heading text). This path exists so the
harvest still produces a recipes corpus on a checkout from before
`docs/recipes` was built, or if it's ever removed. Its record shape:

```
{
  "id": "recipes:<project>:<stage id>" | "recipes:<project>:<file stem>:<section index>",
  "when": "<project> / <stage id or file> — <title or heading>",
  "recipe": {"numbers": {...}, "artifacts": [...]} | "<section body text, capped>",
  "why": "<stage 'does' text>" | "<section heading>",
  "verdict": "passed" | "failed" | "unlabeled",
  "meta": {
    "source_type": "build_plan_stage" | "design_log",
    "project": "<name>", "source_file": "<relative path>",
    "recipe_chars_full": <int> | null, "recipe_truncated": <bool> | null
  }
}
```

(For `design_log` sections in the fallback, `verdict` is
`"passed"`/`"failed"` only when the heading or an `Overall:` line inside
the section says so in those words; otherwise `"unlabeled"` — never
inferred from tone.)

Use: recipe retrieval (RAG-style lookup at generation/check time) and as
qwen2.5-VL judge context — the numeric thresholds and their derivations
are exactly what a render judge needs to cite instead of guessing.

## 4. `render_judgments.jsonl` — one record per render file

Enumerates `projects/<name>/renders/*` (any file, image or video). Pairs
each with the project's reference images, preferring
`design/refs-manifest.json` when present (the authored manifest: file,
tag, note); falling back to a plain directory listing of `design/refs/`
when no manifest exists, tagged as such so the two provenances are never
confused.

A verdict is recorded **only** when the project's design logs actually
name that render file and say something about it — found by searching
`design/*.md` for the render's basename and capturing the containing
paragraph. Most renders currently have no such mention: they are marked
`"unlabeled"` rather than scored, per the brief ("mark records lacking a
verdict as unlabeled rather than inventing one").

```
{
  "id": "render_judgments:<project>:<render filename>",
  "render_path": "projects/<project>/renders/<file>",
  "reference_paths": ["projects/<project>/design/refs/<file>", ...],
  "reference_source": "refs_manifest" | "refs_dir_listing" | "none",
  "verdict_text": "<matched paragraph>" | null,
  "verdict": "labeled" | "unlabeled",
  "meta": {
    "project": "<name>",
    "verdict_source_file": "<relative path>" | null,
    "verdict_chars_full": <int> | null, "verdict_truncated": <bool> | null
  }
}
```

VLM-judge use: `labeled` records are few today but are exactly the
(render, reference, human verdict) triples a render-judge LoRA needs;
`unlabeled` records are inventory for future logging (see
`datasets/README.md`) rather than training signal yet.
