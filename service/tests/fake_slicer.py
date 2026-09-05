"""A stand-in for ``orca-slicer.exe``, so ``/slice`` can be tested anywhere.

The real CLI is only on a machine that has OrcaSlicer installed, and even there
a full slice costs seconds.  This stub answers the same command line, checks the
same things the real binary checks -- the model exists, the profiles exist,
``--outputdir`` was given -- and writes a file where OrcaSlicer would write one:
``plate_1.gcode`` for ``--slice``, the requested name for ``--export-3mf``.

It is pointed at through ``slicer_path``, which
:func:`service.slicer.launcher` runs under the current interpreter because the
path ends in ``.py``.

Behaviour is switched with ``--forge-test-mode`` in ``extra_args``:

===========  ==========================================================
``ok``       (default) write the output file and exit 0
``fail``     exit non-zero with a complaint on stderr
``nofile``   exit 0 having written nothing
``hang``     sleep far past any sane timeout
===========  ==========================================================
"""

from __future__ import annotations

import os
import sys
import time

#: Long enough that any test timeout fires first; the process is killed anyway.
HANG_SECONDS = 300


def main(argv: list) -> int:
    args = list(argv)

    mode = "ok"
    if "--forge-test-mode" in args:
        index = args.index("--forge-test-mode")
        mode = args[index + 1]
        del args[index : index + 2]

    if "--debug" not in args:
        sys.stderr.write("Invalid option: --debug is expected\n")
        return 2
    if "--outputdir" not in args:
        sys.stderr.write("Invalid option: --outputdir is expected\n")
        return 2

    outdir = args[args.index("--outputdir") + 1]
    model = args[-1]

    if not os.path.isfile(model):
        sys.stderr.write(f"can not open model file: {model}\n")
        return 3
    if "--load-settings" in args:
        for profile in args[args.index("--load-settings") + 1].split(";"):
            if profile and not os.path.isfile(profile):
                sys.stderr.write(f"can not find setting file: {profile}\n")
                return 4

    if mode == "hang":
        time.sleep(HANG_SECONDS)
        return 0
    if mode == "fail":
        sys.stderr.write(
            "Slic3r::CLI::run found error, exit\n"
            "the model is outside the print area of the selected printer\n"
        )
        return 13
    if mode == "nofile":
        sys.stdout.write("[warning] cli mode, fake slicer wrote nothing\n")
        return 0

    name = "plate_1.gcode"
    if "--export-3mf" in args:
        name = args[args.index("--export-3mf") + 1]

    with open(os.path.join(outdir, name), "w", encoding="utf-8") as handle:
        handle.write("; fake slicer output\n; model: " + model + "\n")
    sys.stdout.write("[warning] cli mode, fake slicer 0.0.1\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
