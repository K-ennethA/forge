"""Re-derive the frozen mocap selection from the vetted third-party zips.

    python freeze_mocap.py            # rewrite every crop, then verify
    python freeze_mocap.py --check    # verify only: zip member + crop sha256

Any Python 3 works (Blender's bundled one included). Each entry of
PROVENANCE.json names a zip under C:\\forge-assets\\thirdparty (see MANIFEST.md
there: licences, zip hashes), a BVH member inside it and a row range of its
MOTION section (0-based, inclusive). The crop is that member's HIERARCHY
verbatim, "Frames:" rewritten to the kept row count, "Frame Time:" verbatim,
and the kept rows verbatim - no value is touched. build_protagonist_human.py
refuses a crop whose sha256 differs from the one recorded here.

Why crops at all: the CMU cgspeed files open on an added T-pose frame (their
README: "the T pose that I've added is in frame 0"), which is not motion; the
100STYLE takes run 1,600-7,250 rows of a subject turning at the walls of a
small room, and the loop search wants one straight stretch.
"""
import hashlib
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
THIRDPARTY = r"C:\forge-assets\thirdparty"


def crop_bytes(data, first, last):
    text = data.decode("utf-8", errors="strict").splitlines()
    i = next(k for k, line in enumerate(text) if line.strip() == "MOTION")
    frame_time = text[i + 2]
    if not frame_time.lower().startswith("frame time:"):
        raise SystemExit("unexpected MOTION header")
    rows = [line for line in text[i + 3:] if line.strip()]
    if not 0 <= first <= last < len(rows):
        raise SystemExit("rows %d-%d outside the %d the member has" % (first, last, len(rows)))
    body = rows[first:last + 1]
    out = text[:i + 1] + ["Frames: %d" % len(body), frame_time] + body
    return ("\n".join(out) + "\n").encode("utf-8"), len(rows)


def main(check_only):
    prov = json.load(open(os.path.join(HERE, "PROVENANCE.json")))
    bad = 0
    for entry in prov["clips"]:
        src = entry["source"]
        data = zipfile.ZipFile(os.path.join(THIRDPARTY, src["zip"])).read(src["member"])
        member_sha = hashlib.sha256(data).hexdigest()
        blob, total = crop_bytes(data, src["rows"][0], src["rows"][1])
        crop_sha = hashlib.sha256(blob).hexdigest()
        path = os.path.join(HERE, entry["file"])
        if not check_only:
            with open(path, "wb") as fh:
                fh.write(blob)
        on_disk = hashlib.sha256(open(path, "rb").read()).hexdigest() if os.path.isfile(path) else None
        ok = (member_sha == src["member_sha256"] and crop_sha == entry["crop_sha256"]
              and on_disk == crop_sha)
        bad += not ok
        print("%s %-26s member %s crop %s on disk %s (rows %d-%d of %d)"
              % ("OK  " if ok else "FAIL", entry["file"], member_sha[:12], crop_sha[:12],
                 (on_disk or "-")[:12], src["rows"][0], src["rows"][1], total))
    print("%d failed" % bad)
    return bad


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv))
