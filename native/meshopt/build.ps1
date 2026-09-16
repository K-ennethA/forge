# Forge - build meshoptimizer as a Windows x64 DLL for addon/forge/tools/meshopt.py
#
#   .\build.ps1              probe for a toolchain, build meshoptimizer.dll, verify it loads
#   .\build.ps1 -Probe       report which toolchain would be used, build nothing
#   .\build.ps1 -Force       rebuild even when the DLL is newer than the sources
#
# The sources in .\meshoptimizer\ are vendored from github.com/zeux/meshoptimizer at the
# commit recorded in MESHOPT_COMMIT below (MIT, LICENSE.md kept beside them). meshoptimizer
# is dependency-free C++ with a C interface, so the whole library is one compile of
# meshoptimizer\*.cpp - there is no configure step, no third-party header, nothing to fetch.
#
# Nothing here opens a window: every compiler is invoked in-process from this console host
# (no Start-Process, no -Wait on a GUI), per the add-on's headless law.
#
# Toolchains are probed in this order, first hit wins:
#   1. cl.exe     - VS Build Tools / Visual Studio (located through vswhere, vcvars64 sourced)
#   2. clang++    - LLVM for Windows (MSVC ABI by default) or a mingw clang
#   3. cmake      - any generator; a throwaway CMakeLists is written into the build dir
#   4. zig c++    - zig ships its own clang + MSVC-ABI libc shim, needs nothing installed
#
# If none is present the script writes meshopt_build.json with status "blocked" and a reason,
# exits 1, and the add-on keeps working: addon/forge/tools/meshopt.py reports the reason and
# rigforge's LOD stage falls back to Blender's Decimate. Installing any ONE of these unblocks it:
#
#   VS Build Tools   winget install Microsoft.VisualStudio.2022.BuildTools  (add "Desktop
#                    development with C++"), or the installer from visualstudio.microsoft.com
#   LLVM             winget install LLVM.LLVM
#   zig              winget install zig.zig        (smallest: ~50 MB, no system integration)
#
# After a successful build the DLL sits beside this script; deploy copies it into the add-on
# as forge/lib/meshoptimizer.dll, and FORGE_MESHOPT_DLL overrides the search for both.

param([switch]$Probe, [switch]$Force)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$sources = Join-Path $root 'meshoptimizer'
$dll = Join-Path $root 'meshoptimizer.dll'
$manifest = Join-Path $root 'meshopt_build.json'

# Pinned upstream commit. >= 2026-09-09 ("Stabilize many experimental APIs", ce73e457) is a
# hard requirement: meshopt_SimplifyPermissive and meshopt_generateTangents are experimental
# or absent before it, and the v1.2 tag predates both.
$MESHOPT_COMMIT = '3d62f11a3cea144593700a167aff14410fa084e2'
$MESHOPT_COMMIT_DATE = '2026-09-12'

function Write-Manifest([string]$Status, [string]$Toolchain, [string]$Reason) {
    $data = [ordered]@{
        status    = $Status
        commit    = $MESHOPT_COMMIT
        commit_date = $MESHOPT_COMMIT_DATE
        toolchain = $Toolchain
        reason    = $Reason
        built     = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        abi       = 'win-x64'
        dll       = 'meshoptimizer.dll'
    }
    # WriteAllText with an explicit no-BOM encoding: Windows PowerShell 5.1's `Out-File -Encoding
    # utf8` emits a BOM, and a BOM in a JSON file the add-on reads is a parse error in json.load.
    [System.IO.File]::WriteAllText($manifest, ($data | ConvertTo-Json -Depth 4), (New-Object System.Text.UTF8Encoding($false)))
}

function Find-Cl {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vswhere)) { return $null }
    $install = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null
    if (-not $install) { return $null }
    $vcvars = Join-Path $install 'VC\Auxiliary\Build\vcvars64.bat'
    if (-not (Test-Path $vcvars)) { return $null }
    return $vcvars
}

function Import-VcVars([string]$VcVars) {
    # cmd /c so no window is created; the environment is folded back into this session.
    $lines = & cmd.exe /c "`"$VcVars`" >nul 2>&1 && set"
    foreach ($line in $lines) {
        if ($line -match '^([^=]+)=(.*)$') { Set-Item -Path "env:$($matches[1])" -Value $matches[2] -ErrorAction SilentlyContinue }
    }
}

# --- toolchain probe ---------------------------------------------------------
$toolchain = $null
$vcvars = Find-Cl
if ($vcvars) {
    $toolchain = 'cl'
} elseif (Get-Command clang++ -ErrorAction SilentlyContinue) {
    $toolchain = 'clang'
} elseif (Get-Command cmake -ErrorAction SilentlyContinue) {
    $toolchain = 'cmake'
} elseif (Get-Command zig -ErrorAction SilentlyContinue) {
    $toolchain = 'zig'
}

if (-not $toolchain) {
    $reason = 'no C++ toolchain on this machine (probed cl.exe via vswhere, clang++, cmake, zig - none present)'
    Write-Host "  [BLOCKED] $reason"
    Write-Host "  install one of: VS Build Tools (Desktop development with C++), LLVM, or zig - see the header of this script"
    Write-Manifest 'blocked' 'none' $reason
    exit 1
}

Write-Host "  toolchain: $toolchain"
if ($Probe) { exit 0 }

if (-not (Test-Path $sources)) {
    Write-Host "  [FAIL] vendored sources missing: $sources"
    Write-Manifest 'blocked' $toolchain "vendored sources missing at $sources"
    exit 1
}

$cpp = Get-ChildItem "$sources\*.cpp" | Select-Object -ExpandProperty FullName
if (-not $cpp) {
    Write-Host "  [FAIL] no .cpp files under $sources"
    Write-Manifest 'blocked' $toolchain "no .cpp files under $sources"
    exit 1
}

if ((Test-Path $dll) -and -not $Force) {
    $newest = ($cpp | ForEach-Object { (Get-Item $_).LastWriteTimeUtc } | Sort-Object -Descending | Select-Object -First 1)
    if ((Get-Item $dll).LastWriteTimeUtc -gt $newest) {
        Write-Host "  meshoptimizer.dll is up to date (pass -Force to rebuild)"
        exit 0
    }
}

# Exporting the C interface from a DLL is the whole of the configuration: the header leaves
# MESHOPTIMIZER_API empty unless it is defined, and every declaration is already inside
# extern "C", so a plain __declspec(dllexport) gives undecorated cdecl symbols for ctypes.
$define = 'MESHOPTIMIZER_API=__declspec(dllexport)'
$build = Join-Path $root 'build'
New-Item -ItemType Directory -Force -Path $build | Out-Null
$ok = $false

switch ($toolchain) {
    'cl' {
        Import-VcVars $vcvars
        Push-Location $build
        # /LD shared, /O2 speed, /EHsc (the library never throws but MSVC wants the model),
        # /GR- no RTTI - meshoptimizer uses neither.
        & cl.exe /nologo /O2 /LD /EHsc /GR- "/D$define" $cpp "/Fe:$dll" | Out-Host
        $ok = ($LASTEXITCODE -eq 0)
        Pop-Location
    }
    'clang' {
        & clang++ -O2 -shared -fno-rtti -fno-exceptions "-D$define" $cpp -o $dll | Out-Host
        $ok = ($LASTEXITCODE -eq 0)
    }
    'cmake' {
        $lists = @(
            'cmake_minimum_required(VERSION 3.15)',
            'project(meshoptimizer_forge CXX)',
            'set(CMAKE_CXX_STANDARD 11)',
            "file(GLOB MESHOPT_SOURCES `"$($sources -replace '\\','/')/*.cpp`")",
            'add_library(meshoptimizer SHARED ${MESHOPT_SOURCES})',
            'target_compile_definitions(meshoptimizer PRIVATE "MESHOPTIMIZER_API=__declspec(dllexport)")',
            'set_target_properties(meshoptimizer PROPERTIES OUTPUT_NAME meshoptimizer)'
        ) -join "`n"
        [System.IO.File]::WriteAllText((Join-Path $build 'CMakeLists.txt'), $lists, (New-Object System.Text.UTF8Encoding($false)))
        & cmake -S $build -B (Join-Path $build 'out') -A x64 | Out-Host
        if ($LASTEXITCODE -eq 0) { & cmake --build (Join-Path $build 'out') --config Release | Out-Host }
        if ($LASTEXITCODE -eq 0) {
            $made = Get-ChildItem (Join-Path $build 'out') -Recurse -Filter 'meshoptimizer.dll' -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($made) { Copy-Item $made.FullName $dll -Force; $ok = $true }
        }
    }
    'zig' {
        & zig c++ -O2 -shared -target x86_64-windows-msvc -fno-rtti -fno-exceptions "-D$define" $cpp -o $dll | Out-Host
        $ok = ($LASTEXITCODE -eq 0)
    }
}

if (-not $ok -or -not (Test-Path $dll)) {
    $reason = "$toolchain build failed (see output above)"
    Write-Host "  [FAIL] $reason"
    Write-Manifest 'blocked' $toolchain $reason
    exit 1
}

# --- verify the DLL actually loads and the four bound symbols resolve --------
$python = $null
foreach ($cand in @(
    (Join-Path (Split-Path (Split-Path $root -Parent) -Parent) 'service\.venv\Scripts\python.exe'),
    'python.exe')) {
    if ((Test-Path $cand -ErrorAction SilentlyContinue) -or (Get-Command $cand -ErrorAction SilentlyContinue)) { $python = $cand; break }
}
if ($python) {
    $check = @"
import ctypes, sys
lib = ctypes.CDLL(r'$dll')
missing = [n for n in ('meshopt_simplify', 'meshopt_simplifyWithAttributes',
                       'meshopt_generateVertexRemap', 'meshopt_remapIndexBuffer')
           if not hasattr(lib, n)]
print('MISSING ' + ','.join(missing) if missing else 'SYMBOLS OK')
sys.exit(1 if missing else 0)
"@
    $check | & $python - | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [FAIL] built DLL does not export the C interface"
        Write-Manifest 'blocked' $toolchain 'built DLL does not export the C interface'
        exit 1
    }
}

Write-Manifest 'ok' $toolchain ''
Write-Host ("  [OK] {0}  ({1} KB, meshoptimizer {2})" -f $dll, [int]((Get-Item $dll).Length / 1KB), $MESHOPT_COMMIT.Substring(0, 8))
exit 0
