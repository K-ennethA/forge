# Fixture freezing (canonical builder + digest gate)

## WHEN

A test suite (or any repeated measurement) needs the **same** synthetic
geometry on every run so gate thresholds and digests stay meaningful.
Building the fixture fresh through a nondeterministic path (e.g. Quadriflow
retopo) silently invalidates every threshold downstream of it.

## THE RECIPE

1. Build the shared fixture through **one canonical, named, deterministic
   builder** (`headless_rigik.build_character`) rather than letting each
   suite construct its own copy however it likes.
2. **Pin the deterministic route explicitly by name** at every call site
   (`rigforge_retopo method="decimate"`) instead of relying on whatever the
   default happens to be. A nondeterministic default can vary the fixture
   run-to-run without any code change looking suspicious.
3. **Gate the fixture's own geometry digest every run** — a two-run digest
   comparison (coordinates + polygons + weights) — so a change in the
   builder is caught immediately, rather than discovered later as a
   threshold that "moved" for no visible reason.
4. Assertions still derive their bounds from **measured** geometry, as good
   practice, separately from the digest — the digest guards reproducibility,
   not correctness.
5. If a route needs multi-threaded code that is nondeterministic under
   normal threading, know that `OMP_NUM_THREADS=1` **does not fix it**. Only
   a whole-process single-threaded launch (`blender --threads 1`) does, and
   that is a launch flag no in-process call can set — so that route cannot
   be the frozen fixture's route at all. Use the route that is deterministic
   in-process (`decimate`) for anything that must replay exactly.

## WHY

`docs/lane-conventions.md`, quoted directly: *"The shared synthetic
character (`headless_rigik.build_character`) is byte-deterministic since
2026-09-19 — the suite gates its own geometry digest every run... The
product's deterministic retopo route is `rigforge_retopo` `method="decimate"`
(2026-09-19): the fixture asks for it by name and `headless_rigforge` gates
it with a two-run digest comparison. The Quadriflow route stays
nondeterministic at normal threading (225 vertices up to 45.8 mm apart
run-to-run; `OMP_NUM_THREADS=1` changes nothing, only a whole-process
`blender --threads 1` launch stills it, 7/7 byte-identical — a launch flag no
call can set)."*

`docs/architecture.md`, on how the fixture was made to ask for the route by
name: *"Anything that must replay exactly — the shared test character, flows
that pin digests — takes `method="decimate"`. `headless_rigik.build_character`
now asks for it by name instead of monkeypatching `_quad_remesh`, and
`CHARACTER_DIGEST` (unchanged) stays the cross-process pin;
`headless_rigforge.test_retopo_deterministic_route` gates the route with a
two-run digest comparison (coordinates + polygons + weights) and asserts an
unknown `method` is refused by name."*

## REJECTED

- **Monkeypatching `_quad_remesh`** to force determinism from outside —
  replaced by having the fixture ask for `method="decimate"` by name, a
  route that is deterministic on its own terms.
- **Using the default/Quadriflow retopo route** for anything that must
  replay exactly — measured nondeterministic, up to **45.8 mm** of vertex
  drift across runs at normal threading.
- **`OMP_NUM_THREADS=1` as the fix** for Quadriflow's nondeterminism —
  measured: *"changes nothing."* Only a whole-process `--threads 1` launch
  stilled it (7/7 byte-identical), and that is not a flag any in-process
  call can set, which is why Quadriflow was never made the frozen route.

## SOURCE

- `docs/lane-conventions.md` — "Test infrastructure" section.
- `docs/architecture.md` — retopo/LOD determinism section (`method="decimate"`
  fixture pin, `CHARACTER_DIGEST`, `test_retopo_deterministic_route`).
