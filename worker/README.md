# Forge local worker tier

A local coding agent the Claude orchestrator can hand chore-shaped work to, gate
with a deterministic verifier, and retry — so the plan/rate-limit budget goes to
judgment work instead of typing.

This is `docs/automation-thesis.md`'s **weak generator / strong verifier** split
applied to our own build process rather than to geometry. A 14B model running on
the same 12 GB card as everything else is allowed to be wrong as often as it
likes, because nothing it writes survives a gate it cannot argue with. The
orchestrator's job moves from *writing the diff* to *writing the gate* — which is
the part that was always the actual work.

The second reason this tier exists is the vision model: a local VLM for free
buddy pre-screens, so the paid turn is spent on a screenshot that a local judge
already thinks is worth looking at. **That is not wired yet** — it is the next
round, gated on the calibration below.

---

## What is installed, and where

Everything lives **outside the repo** and **outside OneDrive**, because model
weights and a 1.4 GB venv are not things to sync.

| Piece | Version | Location |
|---|---|---|
| Ollama | 0.33.3 | `%LOCALAPPDATA%\Programs\Ollama\ollama.exe` (per-user, no admin) |
| Ollama models | — | `C:\forge-models\ollama` via the `OLLAMA_MODELS` user env var |
| Installer (kept) | 0.33.3 | `C:\forge-models\downloads\OllamaSetup.exe` |
| aider | 0.86.2 | `C:\forge-models\aider-venv\` |

| Model | Tag | Params / quant | Size | Digest |
|---|---|---|---|---|
| Qwen2.5-Coder | `qwen2.5-coder:14b` | 14.8B Q4_K_M | 8,988,124,298 B | `9ec8897f747e2469…` |
| Qwen2.5-VL | `qwen2.5vl:7b` | 8.3B Q4_K_M | 5,969,245,856 B | `5ced39dfa4bac325…` |

Ollama binds its own **11434**, which does not collide with anything in
`docs/architecture.md` (8765 service, 8901 bridge, 9876 Blender socket,
8902/8188 meshgen + ComfyUI).

### The Python situation — read this before touching the venv

`aider-chat` declares `requires_python = ">=3.10,<3.13"`. Every Python this repo
otherwise uses is **3.14**, so aider cannot be installed into the service venv,
the meshgen venv, or the user's base interpreter. It is not a warning you can
ignore — pip refuses outright.

The interpreter the venv is built from is therefore **Blender's own bundled
Python 3.11.13** (`C:\Program Files\Blender Foundation\Blender 5.0\5.0\python\bin\python.exe`),
which is already on the machine, is in aider's supported range, and needed no new
download. The venv is a normal isolated venv — Blender itself is untouched and
never imported — but it *does* depend on that Blender install continuing to
exist. If Blender is ever removed or upgraded to a Python 3.13+ build, rebuild
the venv from any 3.10–3.12 interpreter:

```powershell
& "<some python 3.11 or 3.12>" -m venv C:\forge-models\aider-venv
& C:\forge-models\aider-venv\Scripts\python.exe -m pip install aider-chat
```

`dispatch.py` itself is **stdlib-only** and runs under any Python in the repo
(3.11 or 3.14). It only ever *spawns* aider; it never imports it. So the
`native` backend below keeps working even with no aider at all.

---

## The dispatch protocol

```python
from worker.dispatch import dispatch

report = dispatch({
    "id":             "printer-merge-tests",         # filename-safe; names the run dir
    "brief":          "<markdown>",                  # what to do (see "Writing a brief")
    "files_scope":    ["service/tests/test_x.py"],   # the ONLY files that may change
    "acceptance_cmd": "python -m pytest ... ",       # exit 0 == pass. THIS is the contract
    "cwd":            r"C:\...\some\sandbox",        # where the worker works
    "max_attempts":   2,
})
```

Or from a JSON file (`brief_file` is resolved relative to the JSON):

```powershell
python worker\dispatch.py worker\calibration\tasks\errors-bugfix.json
python worker\dispatch.py <task.json> --backend native --max-attempts 3
```

Optional per-task overrides: `backend`, `model`, `num_ctx`, `temperature`,
`aider_edit_format`, `worker_timeout_s`, `gate_timeout_s`. Defaults live in
`worker/config.json`.

### What a dispatch does

0. **Check the gate is red.** `acceptance_cmd` runs once *before* the worker
   starts, into `gate-precheck.log`. If it already passes, the dispatch stops
   with `GATE_ALREADY_GREEN` and does no work — because a green gate is not
   measuring the task, and every downstream signal ("passed on attempt 1", the
   diff, the wall time) would be meaningless. Usually this means the sandbox
   needs rebuilding after a previous pass. Override with
   `"require_red_gate": false` only when you genuinely mean it.
1. **Snapshot** every path in `files_scope`, byte for byte. Paths that do not
   exist yet are recorded as absent — a task may legitimately ask for a new file,
   and rolling that back means deleting it. There is no git here (by rule); this
   snapshot *is* the rollback.
2. **Run the worker** with the brief and the scoped file list.
3. **Diff** the scoped files against the snapshot, and separately census the
   whole `cwd` so anything the worker scribbled **outside** its scope is recorded
   in `off_scope.json`. That is calibration data, not a hard gate — a worker that
   wanders is a worker you supervise differently.
4. **Run `acceptance_cmd`** in `cwd`. Exit 0 is the only definition of done.
5. On pass: **stop and keep the changes.** On fail: **restore the snapshot**,
   append the gate's actual output to the brief, and try again.
6. Final fail: restore, write a `FAILED` report with `escalate: true`.

A pass with an **empty diff** is downgraded to a failure and annotated
`gate passed with an empty diff -- the gate does not test the task`. This is the
single most valuable line in the file: it catches the gate that was green before
the worker ever ran, which is how a weak-verifier setup quietly becomes a
no-verifier setup.

### How the orchestrator monitors a run

By reading files. Nothing needs to be held open, and a dispatch started in the
background is fully legible from disk while it is still going:

```
worker/runs/<id>/
    task.json                  the task as resolved (defaults filled in)
    brief.md                   the original brief
    gate-precheck.log          the gate, run BEFORE any work (must be red)
    status.txt                 RUNNING -> PASS | FAILED | GATE_ALREADY_GREEN
                               <- poll this one; one word, first line
    attempt-1/
        brief.md               what the worker was ACTUALLY told (attempt 2+
                               has the previous gate output appended)
        prompt.txt             native backend only: the exact prompt sent
        worker.log             worker stdout+stderr
        aider.chat.history.md  aider backend only: the full model transcript
        changes.diff           unified diff vs the snapshot
        off_scope.json         written only if the worker went out of bounds
        gate.log               acceptance_cmd output
        rollback.txt           what was reverted (absent on the passing attempt)
        result.json            per-attempt outcome
    attempt-2/                 ...
    result.json                the final report
```

`status.txt` is one word on the first line. `result.json` carries per-attempt
wall times, the gate return codes, the changed-file list, and `escalate`.

### The escalation rule — two strikes, then a Claude agent

`max_attempts` defaults to **2**, and the second attempt is not a retry of the
same prompt: it gets the failing gate's real output pasted into the brief. If it
still fails, **stop dispatching and hand the task to a Claude agent.** Do not
raise `max_attempts` to brute-force it.

The reason is in the run directory. When a local worker fails twice against a
gate, the near-miss in `attempt-*/changes.diff` almost always names the part of
the brief that was underspecified — read that before rewriting the task, because
the third local attempt usually fails for the same reason the first two did.

### Writing a brief (and a gate) the worker can actually satisfy

The gate is the contract; the brief is a hint. Both fail in predictable ways:

- **The gate must be red before the worker starts.** Run it by hand first. A gate
  that is already green measures nothing, and `dispatch` will call that out but
  only after you have burned the run.
- **The gate must test the task, not the file's existence.** `pytest` reporting
  "1 passed" satisfies a brief that asked for three cases. That is what
  `worker/verifiers/check_test_count.py` is for.
- **Scope narrowly.** `files_scope` is the rollback boundary *and* the whole set
  of files the worker is shown. Adding a file to scope so the model has context
  also gives it permission to rewrite that file.
- **Say what "done" looks like concretely**, with a worked example if the format
  is non-obvious (see `calibration/briefs/glb-docstrings.md`). A 14B model follows
  a shown example far more reliably than a described rule.
- **Do not ask for judgment.** Chores: mechanical tests, docstrings, a bug with a
  failing test already pointing at it, a rename, a format migration. Anything
  where "correct" is a matter of taste belongs to a Claude agent, because there
  is no gate you can write for taste.

---

## Backends

`worker/config.json` picks the default; `--backend` overrides per run.

**`aider`** (default) — spawns aider headless:

```
aider --model ollama_chat/qwen2.5-coder:14b --edit-format whole
      --message-file <brief> --yes-always --no-git --no-auto-commits
      --no-check-update --no-analytics --no-pretty --no-stream
      --no-detect-urls --no-show-model-warnings --map-tokens 0 <files>
```

Flag notes, verified against aider 0.86.2's own `--help`:

- It is **`--yes-always`**. There is no `--yes` flag any more.
- **`--no-git` is required.** aider looks for a repo by default; the sandboxes are
  not repos, and rollback here is the snapshot, not a commit.
- **`--map-tokens 0`.** The repo map is aider's best feature with a frontier model
  and its worst with a 14B one — it spends most of a 16k context on a map of
  files the task does not touch.
- **`ollama_chat/` not `ollama/`**, per aider's own docs, and `OLLAMA_API_BASE`
  must be set (dispatch sets it from `config.json`).

**`native`** — one `POST /api/chat` round-trip, stdlib `urllib`, whole-file
replies parsed and written by `_apply_blocks`. Deliberately *not* unified diffs:
a 14B model emits hunk headers with the wrong line numbers often enough that the
patch applier becomes the thing that fails, and that failure reads like a model
failure when it is a format failure. The files are short; make it rewrite them.
This backend has no dependency on aider, Blender's Python, or a venv at all — it
is the floor the tier cannot fall below.

---

## VRAM — this box has one 12 GB card and everything wants it

`qwen2.5-coder:14b` Q4_K_M is ~9.0 GB of weights. Measured resident with a 16k
context: **~9.6 GB of 12 GB**, so it fits, with roughly 2 GB of headroom and no
room for a second resident model.

Consequences to plan around:

- **meshgen evicts the worker.** TRELLIS.2 peaks at 8.15 GB and Pixal3D at 9.30 GB
  (`docs/architecture.md`, Phase 7). There is not room for both, so a
  `/generate3d` job will push the coder model out. Ollama unloads and reloads on
  its own — nothing breaks — but **the first dispatch after a meshgen job pays a
  cold load of roughly 10–20 s before a single token appears.** Do not read that
  as a hang, and do not use it as a timing measurement.
- **The two models evict each other too.** Coder (9.0 GB) + VL (6.0 GB) cannot be
  resident together. When the buddy pre-screen lands, expect a swap on every
  alternation; batch VLM calls rather than interleaving them with dispatches.
- `keep_alive` is `5m` in `config.json`. Raise it for a burst of dispatches;
  lower it to `0` before running meshgen work to hand the card back immediately.
- Context is the knob that costs VRAM, not the weights: at Q4_K_M this model's KV
  cache runs ~192 KB/token at f16, so 16k ctx is ~1.6 GB (rounded up in the
  measurement above). Doubling `num_ctx` to 32k does **not** fit. If a task needs
  more context, cut `files_scope` instead.
- Check with `ollama ps` (resident models and their context) and `nvidia-smi`.

---

## Calibration

The go/no-go evidence for this tier. Three real chores against real repo code, in
scoped copies under `C:\forge-models\worker-sandboxes\` (the `sandbox_root` key
in `config.json`) — never the live tree.

```powershell
python worker\calibration\setup.py                    # (re)build all three sandboxes
python worker\dispatch.py worker\calibration\tasks\errors-bugfix.json
python worker\dispatch.py worker\calibration\tasks\glb-docstrings.json
python worker\dispatch.py worker\calibration\tasks\printer-merge-tests.json
```

| id | the chore | the gate |
|---|---|---|
| `printer-merge-tests` | three pytest cases for `service/printer.py`'s nested-merge behaviour | ≥3 asserting tests, they pass, the existing printer tests in `test_checks.py` still pass, **and** all three mutants in `mutants.json` are caught |
| `printer-merge-tests-explicit` | the same chore, with the "never assert on a default value" trap named in the brief | identical gate — the variable under test is the brief, not the gate |
| `glb-docstrings` | Google-style docstrings on every public function in `meshgen/glb.py` | `py_compile` + `verifiers/check_docstrings.py` |
| `errors-bugfix` | fix a deliberately broken copy of `service/errors.py` (2 lines) | the sandbox's `tests/test_errors.py` goes green |

Sandboxes are outside `worker/runs/` because `dispatch` deletes and recreates
`runs/<id>/` on every dispatch — a sandbox the dispatcher wipes is a sandbox you
can use once. They are outside the *repo* because the repo is in OneDrive; see
the OneDrive note below. **Rebuild a sandbox before re-running a task that
passed**: a pass leaves the worker's changes in place on purpose, so the next run
would start from them and the gate would be green before it began.

Results from the first calibration run are in `worker/CALIBRATION.md`.

### Windows will fail your gates for reasons that are not your code

Something on Windows — OneDrive on the synced repo, and the indexer or Defender
everywhere else — takes transient handles on files and directories the moment
they are written. It bit three times during the first calibration, twice inside
OneDrive and once on `C:\forge-models\`:

1. A gate ran `py_compile` on a file aider had *just* written and got
   `[Errno 13] Permission denied`. The identical command passed seconds later.
   The worker's docstrings had been perfect. It cost a full attempt.
2. `shutil.rmtree` of the previous `runs/<id>/` died with `WinError 5` and killed
   the dispatch before it started.
3. The same `rmtree`, rebuilding a sandbox on `C:\forge-models\` — nowhere near
   OneDrive — died the same way on `service/samples`.

Fixes shipped, and the reasoning if you are tempted to undo them: sandboxes moved
off the synced drive (`sandbox_root`), because a scratch copy has no business
being synced — but note that (3) proves the synced drive was never the whole
story. `worker/runs/` **stayed** in the repo, since the orchestrator monitors by
reading those files and they are small text. Both delete paths now go through
`dispatch._rmtree_resilient`: clear read-only bits, retry with backoff, and move
the old directory aside rather than refuse to start. Any new code here that
deletes or immediately re-reads a freshly written tree should use it too.

## Verifiers

Reusable gates, all stdlib, all "print reasons then exit non-zero":

- `verifiers/check_docstrings.py <files> [--classes] [--min-functions N]` —
  every public function has a summary line, an `Args:` naming every parameter,
  and a `Returns:` if it returns anything.
- `verifiers/check_test_count.py <file> --min N` — at least N `test_*` functions,
  none of them empty, none of them assertion-free.
- `verifiers/check_tests_catch_mutants.py --module M --tests T --pytest PY
  --mutants-file mutants.json` — **breaks `M` on purpose and requires `T` to
  fail.** A mutant the tests still pass is a survivor and the gate goes red.

The first two take `--min*` floors on purpose: "found 0 things to check,
therefore no problems" is the failure mode every presence-checker has, and the
floor is what turns it back into a red gate.

The third exists because the first two are not enough, and calibration proved it.
Asked for three tests of `normalize_printer`, the worker returned three green,
correctly-named, assert-carrying tests — one of which asserted
`normalize_printer({"nozzle_diameter": 0.4})["nozzle_diameter"] == 0.4`, where
0.4 *is the default*. It tests nothing, and count-plus-green called it OK. When a
worker writes tests, **the gate must be mutation-based** — the tests are the
deliverable, so something other than the tests has to judge them.

## Not yet wired (next round, gated on calibration)

- The VLM buddy pre-screen (`qwen2.5vl:7b` as a free first-pass judge before a
  paid check-in turn). Note the thesis constraint when it lands: **~26 % of VLM
  judgments reverse when presentation order swaps** — any A/B the pre-screen does
  must be order-swap de-biased, and it screens, it does not decide.
- Consensus sampling (sample N, keep the pool-consensus answer; plateaus at
  N ≈ 9) for the cases where one weak sample is not enough.
