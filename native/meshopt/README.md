# native/meshopt — UV-preserving LOD simplification

Blender's Decimate **Collapse** is a position-only quadric with no UV term, so
UV distortion under decimation is unbounded, and its vertex group is a *hard
lock* rather than a soft cost — locking an unwrapped character's seams floors
the reduction far above any LOD budget. This folder is the fix:
[meshoptimizer](https://github.com/zeux/meshoptimizer)'s
`meshopt_simplifyWithAttributes`, which prices UV error into every collapse and
returns an **index buffer into the original vertex buffer**, so a surviving
vertex keeps its original UV bit for bit and every LOD shares LOD0's atlas.

| path | what it is |
|---|---|
| `meshoptimizer/` | vendored library sources (`*.cpp` + `meshoptimizer.h`), MIT |
| `LICENSE.md` | upstream's licence, kept beside the code it covers |
| `build.ps1` | toolchain probe + one-command build of `meshoptimizer.dll` |
| `meshopt_build.json` | written by every build: status, commit, toolchain, reason |
| `meshoptimizer.dll` | the build product (absent until `build.ps1` succeeds) |

Pinned upstream commit: **`3d62f11a3cea144593700a167aff14410fa084e2`** (2026-09-12).
The pin floor is 2026-09-09 (`ce73e457`, "Stabilize many experimental APIs"):
`meshopt_SimplifyPermissive` and `meshopt_generateTangents` are experimental or
absent before it, and the `v1.2` tag predates both. The PyPI `meshoptimizer`
binding is a stale alpha and is deliberately not used.

Only the library sources are vendored — not the demo, gltfpack, the JS bindings
or `extern/` — and the upstream `.git` is not kept: 22 files, ~640 KB, which is
the whole library and nothing else. meshoptimizer is dependency-free C++ with a
C interface, so the build is one compile of `meshoptimizer/*.cpp` with
`MESHOPTIMIZER_API=__declspec(dllexport)`. There is no configure step.

## Building

```powershell
.\build.ps1           # probe, build, verify the C interface loads
.\build.ps1 -Probe    # say which toolchain would be used, build nothing
.\build.ps1 -Force    # rebuild even when the DLL is newer than the sources
```

Toolchains are probed in order — `cl.exe` (via `vswhere`), `clang++`, `cmake`,
`zig c++` — and the first hit wins. Nothing opens a window.

**This machine has none of them** (checked 2026-09-15: no Visual Studio or Build
Tools, no `vswhere`, no LLVM, no cmake, no zig, no mingw; the only WSL
distribution is Docker Desktop's utility VM). `build.ps1` therefore writes
`meshopt_build.json` with `"status": "blocked"` and exits 1, and the add-on runs
on the Decimate fallback with that reason quoted in every LOD report. Installing
any **one** of these unblocks the whole lane, with no code change:

| toolchain | install |
|---|---|
| VS Build Tools | `winget install Microsoft.VisualStudio.2022.BuildTools` + the "Desktop development with C++" workload, or the installer from visualstudio.microsoft.com |
| LLVM | `winget install LLVM.LLVM` |
| zig | `winget install zig.zig` — smallest (~50 MB), no system integration, ships its own clang |

(`winget` itself is not installed here either; the vendor installers work the
same way.)

## How the add-on finds the DLL

`addon/forge/tools/meshopt.py` looks, in order, at

1. `$env:FORGE_MESHOPT_DLL` — an explicit path, which wins over everything;
2. `<installed add-on>/forge/lib/meshoptimizer.dll` — where deploy should copy
   it, because an installed extension cannot see this checkout at all;
3. `<checkout>/native/meshopt/meshoptimizer.dll` — where `build.ps1` leaves it.

When it finds nothing, every entry point still imports, `unavailable_reason()`
returns the sentence from `meshopt_build.json`, and `rigforge`'s LOD stage falls
back to Decimate with `"simplifier": "blender-decimate (meshopt unavailable:
...)"` in the report. The test suite (`addon/tests/headless_meshopt.py`) stays
green either way: the checks that need the library skip with the same reason.
