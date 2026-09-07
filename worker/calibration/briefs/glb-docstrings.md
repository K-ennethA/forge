# Task: Google-style docstrings for every public function in `meshgen/glb.py`

Add a Google-style docstring to every **public** module-level function in
`meshgen/glb.py` — that is, every top-level `def` whose name does not start with
an underscore. `meshgen/glb.py` is the only file you may change.

## What counts as a Google-style docstring here

1. A one-line summary as the first line.
2. An `Args:` section that names **every** parameter, one per line, in the form
   `name: description` or `name (type): description`.
3. A `Returns:` section whenever the function returns a value.

Example of the shape expected:

```python
def widen(box, margin):
    """Grow a bounding box by a uniform margin.

    Args:
        box: The (min, max) corner pair to grow, in millimetres.
        margin: Distance added on every side, in millimetres.

    Returns:
        A new (min, max) corner pair.
    """
```

## Rules

- Change **nothing but docstrings**. No code, no imports, no signatures, no
  reordering, no reformatting of existing lines.
- Keep the existing module-level docstring at the top of the file exactly as it
  is.
- A function that already has a docstring still needs the `Args:` and `Returns:`
  sections added to it — keep its existing prose and extend it.
- Nested helper functions do not need docstrings.
- The file must still import cleanly afterwards.
