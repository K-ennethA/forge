# Task: write three pytest cases for `normalize_printer`'s merge behaviour

Create a NEW file `service/tests/test_printer_merge.py`. It is the only file you
may add or change.

## What the code under test does

`service/printer.py` defines `normalize_printer(profile)`. It deep-copies the
module-level `DEFAULT_PROFILE` and merges the caller's partial `profile` over it:

- For the keys `bed`, `tolerances` and `layer_heights`, the merge is **nested**:
  the caller's sub-keys update the default sub-dict and the default's other
  sub-keys survive.
- For every other key the merge **replaces** the value outright.
- `normalize_printer(None)` returns the defaults unchanged.
- `tolerances` always ends up containing `press_fit` and `magnet_pocket_extra`,
  even if the caller passed a `tolerances` dict that omits them.

## What to write

Exactly three test functions, each covering one part of that behaviour:

1. A partial `bed` override (say `{"bed": {"x": 180.0}}`) keeps the default `y`
   and `z` bed sizes.
2. A partial `tolerances` override keeps the other default tolerances, and
   `press_fit` and `magnet_pocket_extra` are present afterwards.
3. A non-nested key (for example `nozzle_diameter`) is replaced outright, and
   `normalize_printer(None)` equals the defaults.

## Rules

- Import with `from service.printer import DEFAULT_PROFILE, normalize_printer`.
- Plain module-level functions named `test_*`. No test classes, no fixtures.
- Every test must contain at least one real `assert`.
- Do not modify `service/printer.py` or any existing test file.
- Do not add new dependencies; `pytest` is the only import you need beyond the
  service package.
