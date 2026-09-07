# Worker tier calibration — 2026-09-07

The go/no-go evidence for the local worker tier. Three real chores against real
Forge code, in scoped copies, gated deterministically, retried once.

**Verdict: GO, with one hard boundary.** `qwen2.5-coder:14b` driven by aider is
worth dispatching for mechanical chores. It is *not* worth dispatching for
anything where correctness is a matter of judgment — and, importantly, "write
some tests" turned out to be one of those unless the brief names the trap.

The single most useful thing calibration produced is not a pass rate. It is the
discovery that **the gate decides everything, and a gate can be wrong in two
directions** — too weak to notice hollow work, or so wrong that correct work
cannot pass it. Both happened here, in the same task.

---

## Setup

| | |
|---|---|
| Model | `qwen2.5-coder:14b` (14.8B, Q4_K_M, digest `9ec8897f747e2469…`) |
| Runner | Ollama 0.33.3, `127.0.0.1:11434`, models at `C:\forge-models\ollama` |
| Driver | aider 0.86.2, `--edit-format whole`, `--map-tokens 0`, no git |
| Hardware | RTX 5070, 12 GB |
| Sandboxes | `C:\forge-models\worker-sandboxes\<id>\`, rebuilt per run |
| Attempts | 2 (attempt 2 receives the failing gate's real output) |

Every gate was confirmed **red before the worker started** — that check is now
enforced by `dispatch` itself (`gate-precheck.log`, status `GATE_ALREADY_GREEN`).

---

## Results

| # | task | brief | result | attempts | worker time | gate verdict |
|---|---|---|---|---|---|---|
| a | `printer-merge-tests` | plain | **FAILED** | 2 | 15.5 s + 18.6 s | 1 of 3 mutants survived |
| a′ | `printer-merge-tests-explicit` | explicit | **PASS** | 1 | 16.9 s | all 3 mutants caught |
| b | `glb-docstrings` | plain | **PASS** | 1 | 31.6 s | compiles + all docstrings valid |
| c | `errors-bugfix` | plain | **PASS** | 2 | 21.9 s + 26.0 s | tests green |
| c | `errors-bugfix` (`--backend native`) | plain | **PASS** | 1 | 26.4 s | tests green |

Score: **4 of 5 dispatches passed**; the one failure is task (a) on the plain
brief, and rewriting that brief turned it into an attempt-1 pass.

Wall time includes the gate; the printer gate runs pytest four times (count
check, new tests, existing tests, then three mutant builds), so its wall clock is
dominated by the verifier, not the model.

---

## (c) `errors-bugfix` — fix a two-line regression — **PASS**

A copy of `service/errors.py` with two deliberate breaks: `ScriptError.http_status`
changed 400 → 404, and the wire payload key `"traceback"` shortened to `"trace"`.
Gate: the sandbox's `tests/test_errors.py` (9 tests) goes green.

- **Attempt 1** found and fixed *one* of the two bugs (the status code) and
  stopped. Gate red, 2 of 9 tests still failing.
- **Attempt 2**, handed the failing pytest output, fixed the second one.

Both diffs were **minimal and surgical** — exactly the changed lines, no
reformatting, no drive-by edits, no touched docstrings:

```diff
-        return {"error": self.message, "trace": self.traceback_text}
+        return {"error": self.message, "traceback": self.traceback_text}
...
-    http_status = 404
+    http_status = 400
```

**Judgment: excellent.** This is the archetype for the tier. A failing test names
the target precisely, so the model does not have to exercise judgment — and the
retry loop earns its keep, since attempt 1 fixing only half the bug is a very
typical weak-generator outcome that one round of real feedback resolves.

---

## (a) `printer-merge-tests` — write three tests — **FAILED on the plain brief,
PASSED on the explicit one**

This is the interesting one, and it changed the design of the tier.

### What the plain brief produced

Three test functions, correctly named, all green, all carrying real asserts,
existing printer tests still passing. Every cheap gate said OK. And this was one
of them:

```python
def test_non_nested_key_replacement():
    profile = {"nozzle_diameter": 0.4}
    normalized_profile = normalize_printer(profile)
    assert normalized_profile["nozzle_diameter"] == 0.4
```

`0.4` **is the default**. The assertion passes whether the merge works, does
nothing, or is deleted. It is a test-shaped object that tests nothing.

A second test asserted `result["bed"]["y"] == DEFAULT_PROFILE["bed"]["y"]` —
comparing the module to itself, which cannot fail for any mutation of the
default.

### The gate that caught it

`verifiers/check_tests_catch_mutants.py`: patch `service/printer.py` three ways,
and require the new tests to **fail** each time.

| mutant | plain brief | explicit brief |
|---|---|---|
| nested merge disabled for `bed` | caught | caught |
| caller's tolerance overrides ignored | caught | caught |
| non-nested keys dropped instead of replaced | **SURVIVED** | caught |

Attempt 2 of the plain run is worth reading: told that a mutant survived, the
model made a *different* test stricter (exact dict equality), got the expected
dict wrong, and left the tautological test untouched. It pattern-matched "make
assertions stronger"; it did not reason about which assertion was vacuous.

### What the explicit brief changed

One added rule, plus the actual default values:

> **Never assert on a value that equals the default.** A test that overrides
> `nozzle_diameter` with `0.4` proves nothing, because `0.4` is already what
> `DEFAULT_PROFILE` holds.

Result: **passed on attempt 1**, all three mutants caught, and the output is work
a reviewer would accept unchanged:

```python
def test_nested_merge_on_tolerances_with_backfill():
    profile = {"tolerances": {"press_fit": 0.15}}
    normalized_profile = normalize_printer(profile)
    assert normalized_profile["tolerances"]["press_fit"] == 0.15
    assert normalized_profile["tolerances"]["slide_fit"] == 0.2
    assert normalized_profile["tolerances"]["loose_fit"] == 0.3
```

**Judgment: capable, but only with the trap named.** The model cannot find the
failure mode itself; told about it in one sentence, it applies the rule
correctly and generally. Which is exactly what a weak generator is supposed to
do, and exactly why the brief is orchestrator work.

### The gate was wrong first, and that is the sharpest lesson

The mutation gate initially carried a mutant that disabled the
`for required in ("press_fit", "magnet_pocket_extra"): setdefault(...)` backfill
in `normalize_printer`. **That mutant is unkillable.** Because the nested merge
seeds `tolerances` from `DEFAULT_PROFILE` before the backfill runs, those keys
are always already present; the loop is unreachable through the public API.

No correct test could ever have passed that gate. Two calibration runs were
judged against it before the dead code was spotted — including one where the
worker's tests were fine.

A strong verifier that is *wrong* is worse than a weak one, because it looks
rigorous while rejecting good work, and the run report blames the model. The fix
shipped as a rule with a tool behind it: `setup.py --verify-mutants` runs the
mutants against a hand-written reference answer and refuses to bless the gate
unless every mutant is caught. **Prove the gate is winnable before trusting a
single verdict it returns.**

---

## (b) `glb-docstrings` — docstrings for every public function

Gate: `py_compile` + `verifiers/check_docstrings.py` (summary line, `Args:`
naming every parameter, `Returns:` when the function returns).

**PASS on attempt 1, 31.6 s.** Both public functions got a summary line, an
`Args:` naming `path`, and a `Returns:`. `read_json_chunk` had no docstring at
all; `stats` had one that the checker rejected for missing both sections, and the
model extended it rather than replacing it, as the brief asked. No code changed.

**Judgment: good, with one caveat worth knowing.** Because `--edit-format whole`
makes aider rewrite the file, it came back with CRLF line endings, so the
*textual* diff is the entire file even though the *semantic* diff is four
docstrings. Read `changes.diff` for this kind of task with that in mind, or the
change looks far bigger than it is.

This task also produced the tier's most instructive false negative. On the first
run it FAILED attempt 1 with `[Errno 13] Permission denied: 'meshgen/glb.py'`
from `py_compile` — while the docstrings it had just written were **perfect**.
OneDrive had taken a handle on the fresh write; the same command passed seconds
later by hand. Once the sandboxes moved off the synced drive the task passed on
attempt 1 every time. A flaky gate does not read as a flaky gate in the run
report — it reads as a model failure, and would have quietly biased the whole
go/no-go decision.

---

## The backends

Both were run against task (c), same sandbox, same model.

| backend | result | attempts | worker time | diff |
|---|---|---|---|---|
| `aider` 0.86.2 | PASS | 2 | 21.9 s + 26.0 s | minimal, both bugs |
| `native` (our own, stdlib `urllib`) | **PASS** | **1** | **26.4 s** | minimal, both bugs |

The 60-line fallback backend **beat aider on this task**, fixing both bugs in a
single round-trip where aider needed the retry. Not a general claim — one task,
one sample — but it settles the question the fallback existed to answer: the tier
does not depend on aider, on Blender's Python 3.11, or on a venv at all. If
`aider-chat` ever breaks, `--backend native` is a real worker, not a stub.

The aider path is reproducible: two separate runs of task (c) both took exactly
two attempts, fixing the status code first and the payload key second.

Expect one harmless line in every aider `worker.log`:
`Can't initialize prompt toolkit: No Windows console found` — that is
`CREATE_NO_WINDOW` doing its job, not an error.

---

## VRAM, measured

| | |
|---|---|
| Idle desktop | ~1.4 GB |
| `qwen2.5-coder:14b` resident, ctx 10 343 | **10.35 GB in VRAM, 11.3 GB total**, reported `8%/92% CPU/GPU` |
| Whole GPU during a dispatch | ~11.5 GB of 12.2 GB, 50–60 % utilisation |
| `qwen2.5vl:7b` resident | 5.49 GB (loading it **evicted** the coder model) |

The model **does not fully fit** at aider's auto-sized context: Ollama offloads
about 8 % of the layers to CPU. It still runs at usable speed (15–60 s per
chore), but there is no headroom.

Consequences, all confirmed against `docs/architecture.md`'s Phase 7 numbers:

- **meshgen and the worker cannot coexist.** TRELLIS.2 peaks at 8.15 GB and
  Pixal3D at 9.30 GB. A `/generate3d` job evicts the coder model; the next
  dispatch pays a cold reload before its first token. Nothing breaks — do not
  read the pause as a hang, and do not time a dispatch that follows a meshgen job.
- **The two Ollama models evict each other**: coder 9.0 GB + VL 6.0 GB do not
  fit together. When the buddy pre-screen lands, batch the VLM calls rather than
  alternating them with dispatches.
- **Do not raise `num_ctx`.** KV cache runs ~192 KB/token at f16 for this model,
  so 16k ctx is ~1.6 GB and 32k does not fit at all. If a task needs more
  context, cut `files_scope` instead.

---

## Refinements this calibration forced

1. **`check_tests_catch_mutants.py`** — mutation gating. When the deliverable
   *is* tests, no gate made of those tests can judge them.
2. **`--verify-mutants`** — prove the gate is winnable against a reference
   answer before trusting its verdict.
3. **Red-gate precondition in `dispatch`** — `acceptance_cmd` runs before the
   worker; an already-green gate stops the run as `GATE_ALREADY_GREEN` instead
   of producing a meaningless "passed on attempt 1".
4. **Empty-diff downgrade** — a pass with no change to any scoped file is
   recorded as a failure with the reason spelled out.
5. **`_rmtree_resilient`** — Windows (OneDrive, and the indexer/Defender
   elsewhere) holds transient handles on freshly written trees. This cost one
   full attempt to a spurious `[Errno 13]` on a `py_compile` whose input was
   perfect, and killed two dispatches outright with `WinError 5`.
6. **Sandboxes moved off the synced drive** (`sandbox_root` in `config.json`).
7. **Off-scope census normalised to forward slashes** — `os.walk` returns
   backslashes on Windows, so every scoped file was being reported as an
   off-scope modification.
8. **aider's history files pinned into the run directory.** Left alone, aider
   anchors `.aider.chat.history.md` to the git root it detects — which it still
   detects under `--no-git` — so the first run wrote its transcript into the top
   of the Forge repo. Now `--chat-history-file`/`--input-history-file` point at
   `runs/<id>/attempt-N/`, which turns litter into evidence.

## Vision model

`qwen2.5vl:7b` was smoke-tested end to end through `/api/chat` with a base64 PNG
(a red circle above a blue square): correct answer — *"a red circle and a blue
square… the red circle is positioned above the blue square"* — in 12.7 s
including the cold load. The vision path works. It is **not wired into anything**;
that is the next round.

---

## How to use this tier

**Dispatch it for:** a bug with a failing test already pointing at it;
docstrings and other mechanical annotation; renames and format migrations;
tests **when the brief names the traps and the gate is mutation-based**.

**Do not dispatch it for:** anything where "correct" is a matter of taste, or
where you cannot write a gate that is red before the work and green only after
correct work. There is no gate for judgment, and a green gate over hollow work
is worse than no worker at all — it consumes the orchestrator's trust, which is
the one thing this tier exists to conserve.

**Budget:** 15–90 s per chore, plus gate time. Two attempts, then escalate. The
second attempt is not a re-roll — it carries the real gate output — and when it
also fails, the near-miss in `attempt-*/changes.diff` reliably names the part of
the brief that was underspecified. That is what happened in (a), and rewriting
the brief fixed it on the next attempt-1.
