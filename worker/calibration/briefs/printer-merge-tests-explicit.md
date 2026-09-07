# Task: write three pytest cases for `normalize_printer`'s merge behaviour

Create a NEW file `service/tests/test_printer_merge.py`. It is the only file you
may add or change.

## What the code under test does

`service/printer.py` defines `normalize_printer(profile)`. It deep-copies the
module-level `DEFAULT_PROFILE` and merges the caller's partial `profile` over it:

- For the keys `bed`, `tolerances` and `layer_heights` the merge is **nested**:
  the caller's sub-keys update the default sub-dict and the default's other
  sub-keys survive.
- For every other key the merge **replaces** the value outright.
- `normalize_printer(None)` returns the defaults unchanged.
- `tolerances` always ends up containing `press_fit` and `magnet_pocket_extra`,
  even if the caller passed a `tolerances` dict that omits them.

The relevant defaults, so you do not have to guess them:

```python
"bed":        {"x": 256.0, "y": 256.0, "z": 256.0}
"nozzle_diameter": 0.4
"tolerances": {"press_fit": 0.1, "slide_fit": 0.2,
               "loose_fit": 0.3, "magnet_pocket_extra": 0.05}
```

## THE RULE THAT MATTERS MOST

**Never assert on a value that equals the default.** A test that overrides
`nozzle_diameter` with `0.4` proves nothing, because `0.4` is already what
`DEFAULT_PROFILE` holds — the assertion passes whether the merge works or not.
Every override you write must use a value that is *different* from the default,
so that the assertion can only pass if the merge actually happened.

The same applies to what you assert *survived*: name the specific default keys
you expect to still be there and assert their values, rather than only checking
that the key you just set is present.

## What to write

Exactly three test functions:

1. **Nested merge on `bed`.** Override only `x`, with a value that is not 256.0.
   Assert the new `x`, and assert `y` and `z` are still 256.0.
2. **Nested merge on `tolerances`, including the backfill.** Override only
   `press_fit`, with a value that is not 0.1. Assert the new `press_fit`; assert
   `slide_fit` is still 0.2 and `loose_fit` is still 0.3. Then, in the same test,
   call `normalize_printer` again with a `tolerances` dict that contains **only**
   `slide_fit` and assert that `press_fit` and `magnet_pocket_extra` are still
   present with their default values — that is the backfill, and it is only
   exercised when the caller's dict omits them.
3. **Non-nested replacement.** Override `nozzle_diameter` with a value that is
   not 0.4 and assert the result equals your value. Then assert
   `normalize_printer(None) == DEFAULT_PROFILE`.

## Rules

- Import with `from service.printer import DEFAULT_PROFILE, normalize_printer`.
- Plain module-level functions named `test_*`. No test classes, no fixtures.
- Every test must contain at least one real `assert`.
- Do not modify `service/printer.py` or any existing test file.
- Do not add new dependencies.
