# Task: `tests/test_errors.py` is failing — fix `service/errors.py`

The test suite in `tests/test_errors.py` is red. The bug is in
`service/errors.py`, which is the only file you may change. **Do not touch the
tests** — they are correct and they encode the contract.

## The contract the module must honour

From `docs/architecture.md`, the geometry service's HTTP error contract is:

- Anything the caller can fix by editing their script or their overrides is
  **HTTP 400** — that is `ScriptError` and everything derived from it
  (`ParamError`, `TimeoutError_`).
- Anything that is the service's own fault is **HTTP 500** — the `ForgeError`
  base and `ServiceError`.
- The wire body is exactly two keys:

  ```json
  {"error": "<message>", "traceback": "<traceback text or null>"}
  ```

## How to work

Run the tests, read the failures, and change the smallest number of lines in
`service/errors.py` that makes them pass. Do not add new classes, do not rename
anything, and do not weaken a test by changing what the module exports.
