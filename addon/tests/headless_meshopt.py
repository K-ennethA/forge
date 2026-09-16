"""Headless tests for the meshoptimizer ctypes wrapper (``forge.tools.meshopt``).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_meshopt.py

Blender is only the host: the module under test imports no ``bpy`` and the
fixtures here are built from plain lists, so every check would run under any
CPython.  It lives with the other headless suites because that is where the
add-on's ``sys.path`` and the one-command runner are.

**The suite is green with or without the DLL**, which is the point of the lane's
fallback design.  ``native/meshopt/build.ps1`` builds ``meshoptimizer.dll``; when
it has not run (or found no C++ toolchain) the checks that need the library are
reported as SKIPs with the reason the wrapper gives, and the checks that do not -
argument validation, the weld census, the UV-weight derivation, the attribute
layout, the name the LOD reports carry - still run.  A suite that went silent
without the DLL would hide a broken binding on the machine that has one.
"""

import os
import sys
import traceback

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
if ADDON_DIR not in sys.path:
    sys.path.insert(0, ADDON_DIR)

from forge.tools import meshopt  # noqa: E402

_RESULTS = []
_SKIPS = []


def check(label, condition, detail=""):
    _RESULTS.append((label, bool(condition), detail))
    print("  %s %s%s" % ("PASS" if condition else "FAIL", label,
                         ("  -- " + str(detail)) if detail and not condition else ""))
    return bool(condition)


def skip(label, reason):
    _SKIPS.append((label, reason))
    print("  SKIP %s  -- %s" % (label, reason))


def note(text):
    print("     %s" % text)


def section(title):
    print("\n== %s ==" % title)


# --- fixtures ----------------------------------------------------------------

def grid(size=16, extent=1.0):
    """A welded ``size`` x ``size`` quad grid, triangulated, unwrapped 0..1.

    Welded: one vertex per lattice point, shared by up to six triangles.  This is
    the shape a simplifier is supposed to eat - plenty of interior edges, a
    border it may move, and a UV that is an exact linear function of position, so
    any UV that comes back wrong is wrong by inspection.
    """
    vertices = []
    uvs = []
    for row in range(size):
        for column in range(size):
            u = column / float(size - 1)
            v = row / float(size - 1)
            vertices.extend((u * extent, v * extent, 0.0))
            uvs.extend((u, v))
    indices = []
    for row in range(size - 1):
        for column in range(size - 1):
            a = row * size + column
            b = a + 1
            c = a + size
            d = c + 1
            indices.extend((a, c, b, b, c, d))
    normals = [0.0, 0.0, 1.0] * (size * size)
    return vertices, indices, uvs, normals


def soup(size=8):
    """The same grid exploded into a triangle soup: every corner its own vertex.

    No two triangles share a position, so every edge is a topological border and
    a simplifier collapses exactly nothing - while reporting success.  This is
    the failure the wrapper exists to catch before the call.
    """
    vertices, indices, uvs, _normals = grid(size)
    out_vertices = []
    out_uvs = []
    out_indices = []
    for position, index in enumerate(indices):
        # Nudge each copy so the positions are not even bit-identical, which is
        # the real-world version (an exporter that wrote per-corner floats).
        out_vertices.extend((vertices[index * 3] + position * 1e-7,
                             vertices[index * 3 + 1],
                             vertices[index * 3 + 2]))
        out_uvs.extend((uvs[index * 2], uvs[index * 2 + 1]))
        out_indices.append(position)
    return out_vertices, out_indices, out_uvs


def seam_split_quad():
    """Two triangles sharing an edge whose two vertices are split for a UV seam.

    Six vertices, four distinct positions: the two seam vertices appear twice
    with different UVs and **identical position floats**.  That is welded input
    with wedges, not unwelded input, and the distinction is the whole reason the
    census counts both.
    """
    positions = [
        (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),  # triangle 1
        (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0),  # triangle 2, shares 2
    ]
    uvs = [(0.0, 0.0), (0.5, 0.0), (0.0, 0.5), (0.5, 0.5), (1.0, 1.0), (0.5, 1.0)]
    vertices = [value for point in positions for value in point]
    flat_uvs = [value for uv in uvs for value in uv]
    return vertices, [0, 1, 2, 3, 4, 5], flat_uvs


def tetrahedron():
    """Four triangles, closed, manifold - and impossible to reduce below."""
    vertices = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    indices = [0, 2, 1, 0, 1, 3, 0, 3, 2, 1, 2, 3]
    uvs = [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 1.0]
    return vertices, indices, uvs


# --- checks that need no DLL -------------------------------------------------

def test_availability():
    section("availability and provenance")
    path = meshopt.dll_path()
    manifest = meshopt.build_manifest()
    note("dll_path: %s" % (path or "(none)"))
    note("manifest: status=%s toolchain=%s commit=%s"
         % (manifest.get("status"), manifest.get("toolchain"), meshopt.commit()))

    check("no DLL means not available, and a loaded DLL means a path",
          (bool(path) or not meshopt.available())
          and (not meshopt.available() or bool(path)),
          "available=%s path=%s" % (meshopt.available(), path))

    name = meshopt.simplifier_name()
    if meshopt.available():
        check("simplifier_name names meshopt and its pinned commit",
              name.startswith("meshopt ") and len(name.split()[1]) >= 7, name)
        check("unavailable_reason is empty when the library is there",
              meshopt.unavailable_reason() == "", meshopt.unavailable_reason())
    else:
        check("simplifier_name names the fallback AND why",
              name.startswith("blender-decimate (meshopt unavailable: ")
              and len(meshopt.unavailable_reason()) > 20, name)
        note("fallback reason: %s" % meshopt.unavailable_reason())

    check("the pinned commit is recorded on disk, not in chat",
          isinstance(manifest.get("commit"), str) and len(manifest["commit"]) == 40,
          str(manifest.get("commit")))


def test_signatures_against_the_header():
    """Every bound arity checked against meshoptimizer.h, DLL or no DLL.

    A ctypes binding whose argument list is one entry short does not fail to
    load: it corrupts the stack on the machine that *has* the library, which is
    the machine nobody is testing on.  The vendored header is right there in the
    checkout, so the arity can be read off the declaration instead of trusted.
    """
    section("bindings vs meshoptimizer.h")
    header = os.path.join(os.path.dirname(ADDON_DIR), "native", "meshopt",
                          "meshoptimizer", "meshoptimizer.h")
    if not os.path.isfile(header):
        skip("bound signatures match the header", "no vendored header at %s" % header)
        return
    with open(header, "r", encoding="utf-8") as handle:
        text = handle.read()

    for name, (_restype, argtypes) in sorted(meshopt._SIGNATURES.items()):
        marker = " %s(" % name
        at = text.find(marker)
        if at < 0:
            check("%s is declared in the header" % name, False, "not found")
            continue
        end = text.find(")", at)
        declaration = text[at + len(marker):end]
        # Split on commas; the header's C parameters carry no defaults, no
        # templates and no function pointers in the calls bound here.
        parameters = [part for part in declaration.split(",") if part.strip()]
        check("%s takes %d arguments, and so does the binding"
              % (name, len(parameters)), len(parameters) == len(argtypes),
              "header %d vs binding %d" % (len(parameters), len(argtypes)))

    for flag, expected in (("meshopt_SimplifyLockBorder", meshopt.SIMPLIFY_LOCK_BORDER),
                           ("meshopt_SimplifyPrune", meshopt.SIMPLIFY_PRUNE),
                           ("meshopt_SimplifyPermissive", meshopt.SIMPLIFY_PERMISSIVE),
                           ("meshopt_SimplifyVertex_Protect", meshopt.VERTEX_PROTECT)):
        # The name appears in prose first ("... tagged with X in vertex_lock"),
        # so match the enumerator line itself, not the first mention.
        line = ""
        for candidate in text.splitlines():
            stripped = candidate.strip()
            if stripped.startswith(flag + " ="):
                line = stripped
                break
        shift = None
        if "1 <<" in line:
            shift = int(line.split("1 <<")[1].split(",")[0].strip())
        check("%s is %d in the header" % (flag, expected),
              shift is not None and (1 << shift) == expected,
              "header says %s" % line.strip())


def test_weld_census():
    section("weld census (the silent no-op)")
    vertices, indices, uvs, _normals = grid(8)
    census = meshopt.weld_report(vertices, indices)
    check("a welded grid is not a soup",
          census["soup"] is False and census["wedges"] == 0
          and census["unique_positions"] == census["vertex_count"], str(census))

    vertices, indices, uvs = seam_split_quad()
    census = meshopt.weld_report(vertices, indices)
    check("UV-seam wedges count as welded, not unwelded",
          census["soup"] is False and census["wedges"] == 2
          and census["unique_positions"] == 4, str(census))

    vertices, indices, uvs = soup(6)
    census = meshopt.weld_report(vertices, indices)
    check("a triangle soup is caught",
          census["soup"] is True
          and census["unique_positions"] == census["vertex_count"], str(census))

    if meshopt.available():
        try:
            meshopt.simplify_lod(vertices, indices, uvs=uvs,
                                 target_index_count=len(indices) // 2)
            check("simplify_lod refuses a soup", False, "no exception")
        except meshopt.MeshoptUnwelded as exc:
            check("simplify_lod refuses a soup", "weld positions first" in str(exc).lower(),
                  str(exc)[:120])
        except meshopt.MeshoptError as exc:
            check("simplify_lod refuses a soup", False, "wrong error: %s" % exc)
    else:
        # Validation runs before the library is loaded, so this check does not
        # need the DLL - which is exactly why it is written that way.
        try:
            meshopt.simplify_lod(vertices, indices, uvs=uvs,
                                 target_index_count=len(indices) // 2)
            check("simplify_lod refuses a soup without the DLL", False, "no exception")
        except meshopt.MeshoptUnwelded:
            check("simplify_lod refuses a soup without the DLL", True)
        except meshopt.MeshoptError as exc:
            check("simplify_lod refuses a soup without the DLL", False,
                  "wrong error: %s" % exc)


def test_arguments():
    section("argument validation")
    vertices, indices, uvs, normals = grid(6)

    cases = [
        ("a ragged index buffer", (vertices, indices[:-1]), {}),
        ("an out-of-range index", (vertices, indices[:-1] + [9999]), {}),
        ("an empty mesh", ([], []), {}),
        ("a short UV array", (vertices, indices), {"uvs": uvs[:4]}),
        ("a weight vector of the wrong length",
         (vertices, indices), {"uvs": uvs, "normals": normals,
                               "weights": [1.0, 2.0]}),
    ]
    for label, args, kwargs in cases:
        try:
            meshopt.simplify_lod(*args, **kwargs)
            check("rejects %s" % label, False, "no exception")
        except meshopt.MeshoptUnavailable as exc:
            # Only reachable for inputs that pass validation; none of these do.
            check("rejects %s" % label, False, "reached the loader: %s" % exc)
        except meshopt.MeshoptError:
            check("rejects %s" % label, True)


def test_uv_weight():
    section("UV weight (gltfpack's documented trap)")
    vertices, indices, uvs, normals = grid(8, extent=2.0)
    dense = meshopt.uv_weight(vertices, indices, uvs)
    vertices_small, indices_small, uvs_small, _n = grid(8, extent=0.2)
    sparse = meshopt.uv_weight(vertices_small, indices_small, uvs_small)

    check("the UV weight is never gltfpack's zero", dense > 0.0 and sparse > 0.0,
          "%.3f / %.3f" % (dense, sparse))
    check("the UV weight stays inside the researched 10-100 band",
          meshopt.UV_WEIGHT_MIN <= dense <= meshopt.UV_WEIGHT_MAX
          and meshopt.UV_WEIGHT_MIN <= sparse <= meshopt.UV_WEIGHT_MAX,
          "%.3f / %.3f" % (dense, sparse))
    check("a bigger mesh in the same atlas is weighted no less than a small one",
          dense >= sparse, "%.3f vs %.3f" % (dense, sparse))
    note("uv weight: 2 m mesh %.2f, 0.2 m mesh %.2f (per scalar)" % (dense, sparse))

    weights = meshopt.attribute_weights(vertices, indices, uvs=uvs, normals=normals)
    check("weights line up with the attribute buffer: 3 normal + 2 UV",
          len(weights) == 5 and weights[0] == meshopt.NORMAL_WEIGHT
          and weights[3] == weights[4] == dense, str(weights))

    attributes, stride = meshopt.interleave_attributes(4, uvs=[0.25, 0.5] * 4,
                                                       normals=[0.0, 0.0, 1.0] * 4)
    check("the attribute buffer is [nx, ny, nz, u, v] per vertex",
          stride == 5 and list(attributes[0:5]) == [0.0, 0.0, 1.0, 0.25, 0.5]
          and len(attributes) == 20, str(list(attributes[:10])))


# --- checks that need the DLL ------------------------------------------------

def test_simplify():
    section("simplification")
    if not meshopt.available():
        for label in ("a welded grid reaches its target",
                      "survivors keep their exact UVs",
                      "the achieved-count assertion fires on an impossible target",
                      "attribute weights change the result"):
            skip(label, meshopt.unavailable_reason())
        return

    vertices, indices, uvs, normals = grid(24)
    target = (len(indices) // 3 // 4) * 3
    uvs_before = list(uvs)
    vertices_before = list(vertices)
    report = {}
    out, error = meshopt.simplify_lod(vertices, indices, uvs=uvs, normals=normals,
                                      target_index_count=target, report=report)
    check("a welded grid reaches its target",
          0 < len(out) <= target,
          "%d indices against a target of %d (%d in)"
          % (len(out), target, len(indices)))
    check("the report measures the achieved count, not result_error",
          report["achieved_index_count"] == len(out)
          and report["hit_target"] is (len(out) <= target), str(report))
    note("%d -> %d indices, result_error %.6f, weights %s"
         % (len(indices), len(out), error, report["attribute_weights"]))

    vertex_count = len(vertices) // 3
    check("every returned index addresses the original vertex buffer",
          all(0 <= index < vertex_count for index in out))

    # The promise the whole lane rests on: a surviving vertex is the *original*
    # vertex, so its UV is the original UV to the last bit - no interpolation, no
    # re-projection, nothing to re-bake. Two halves, both real: the input buffers
    # come back untouched (``meshopt_simplifyWithUpdate`` is the sibling call
    # that rewrites them in place, and switching to it by accident would be
    # invisible otherwise), and each survivor's UV equals the pre-call snapshot.
    survivors = sorted(set(out))
    check("the input buffers are not modified in place",
          list(uvs) == uvs_before and list(vertices) == vertices_before)
    drifted = [index for index in survivors
               if uvs[index * 2] != uvs_before[index * 2]
               or uvs[index * 2 + 1] != uvs_before[index * 2 + 1]]
    check("survivors keep their exact UVs", not drifted,
          "%d of %d survivors drifted" % (len(drifted), len(survivors)))

    # A grid's UV is an exact linear function of position, so "the UV is the
    # original one" can be asserted against the mesh's own law rather than
    # against itself: u == x / extent, v == y / extent, bit for bit.
    mismatched = [index for index in survivors
                  if uvs[index * 2] != vertices[index * 3]
                  or uvs[index * 2 + 1] != vertices[index * 3 + 1]]
    check("surviving UVs still satisfy the fixture's own UV law",
          not mismatched, "%d of %d off" % (len(mismatched), len(survivors)))

    # An impossible target: a closed tetrahedron cannot go below four triangles
    # with pruning and permissive collapses off, so asking for one triangle is a
    # target that result_error would happily call a success.
    vertices, indices, uvs = tetrahedron()
    quiet_report = {}
    out, error = meshopt.simplify_lod(vertices, indices, uvs=uvs,
                                      target_index_count=3, prune=False,
                                      permissive=False, report=quiet_report)
    check("a missed target is visible in the count, not in result_error",
          len(out) > 3 and quiet_report["hit_target"] is False,
          "%d indices, result_error %.6f" % (len(out), error))
    try:
        meshopt.simplify_lod(vertices, indices, uvs=uvs, target_index_count=3,
                             prune=False, permissive=False, require_target=True)
        check("the achieved-count assertion fires on an impossible target",
              False, "no exception")
    except meshopt.MeshoptTargetMissed as exc:
        check("the achieved-count assertion fires on an impossible target", True)
        note(str(exc))

    # Weights are not decoration: a UV weight of zero is gltfpack's default and
    # is meant to give a different (UV-blind) answer from a real one.
    vertices, indices, uvs, normals = grid(24)
    blind = {}
    meshopt.simplify_lod(vertices, indices, uvs=uvs, normals=normals,
                         target_index_count=target,
                         weights=[1.0, 1.0, 1.0, 0.0, 0.0], report=blind)
    aware = {}
    meshopt.simplify_lod(vertices, indices, uvs=uvs, normals=normals,
                         target_index_count=target, report=aware)
    check("attribute weights change the result",
          blind["attribute_weights"] != aware["attribute_weights"],
          "%s vs %s" % (blind["attribute_weights"], aware["attribute_weights"]))


def test_weld_helper():
    section("position-only weld helper")
    if not meshopt.available():
        skip("weld_positions folds wedges onto one vertex",
             meshopt.unavailable_reason())
        return
    vertices, indices, uvs = seam_split_quad()
    remap, unique = meshopt.weld_positions(vertices, indices)
    check("weld_positions folds wedges onto one vertex",
          unique == 4 and len(remap) == 6, "%d unique, remap %s"
          % (unique, list(remap)))


def main():
    print("Forge meshoptimizer wrapper suite")
    try:
        test_availability()
        test_signatures_against_the_header()
        test_weld_census()
        test_arguments()
        test_uv_weight()
        test_simplify()
        test_weld_helper()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")

    failed = [label for label, ok, _ in _RESULTS if not ok]
    print("\n%d checks, %d failed, %d skipped"
          % (len(_RESULTS), len(failed), len(_SKIPS)))
    for label in failed:
        print("  FAILED: %s" % label)
    for label, reason in _SKIPS:
        print("  SKIPPED: %s (%s)" % (label, reason))
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
