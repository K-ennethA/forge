"""Acceptance gate for the electronics_motors.py data chore.

Run:  python check_motor_components.py <path-to-service-dir>

Asserts the data module is importable, populated, and every entry is a
complete, sane, never-auto-purchase record.  Exits 0 on pass, 1 with a
sentence per failure otherwise.
"""

import importlib.util
import sys

REQUIRED_KEYS = {"name", "kind", "size_mm", "voltage_v", "current_ma",
                 "purchase_link", "purchase_note", "caveats"}
ALLOWED_KINDS = {"motor", "fan", "driver", "sensor", "battery"}
FORBIDDEN_URL_BITS = ("cart", "checkout", "buy", "add-to", "affiliate", "tag=")
MIN_ENTRIES = 8
MIN_KINDS = 4


def fail(msg):
    print("FAIL: " + msg)
    fail.count += 1


fail.count = 0


def main(service_dir):
    spec = importlib.util.spec_from_file_location(
        "electronics_motors", service_dir + "/electronics_motors.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    if not (mod.__doc__ or "").strip().startswith(
            "Motor, fan and sensor component entries"):
        fail("the module docstring header was rewritten; restore it")

    comps = getattr(mod, "MOTOR_COMPONENTS", None)
    if not isinstance(comps, dict):
        fail("MOTOR_COMPONENTS must be a dict")
        return
    if len(comps) < MIN_ENTRIES:
        fail("need at least %d entries, found %d" % (MIN_ENTRIES, len(comps)))

    kinds_seen = set()
    for key, entry in comps.items():
        where = "entry %r" % key
        if not isinstance(entry, dict):
            fail(where + " is not a dict")
            continue
        missing = REQUIRED_KEYS - set(entry)
        if missing:
            fail(where + " is missing keys: " + ", ".join(sorted(missing)))
            continue
        if entry["kind"] not in ALLOWED_KINDS:
            fail(where + " has kind %r, not one of %s"
                 % (entry["kind"], sorted(ALLOWED_KINDS)))
        kinds_seen.add(entry["kind"])
        size = entry["size_mm"]
        if (not isinstance(size, (list, tuple)) or len(size) != 3
                or not all(isinstance(v, (int, float)) and 0 < v < 400
                           for v in size)):
            fail(where + " size_mm must be [w, d, h] in (0, 400) mm")
        volts = entry["voltage_v"]
        if (not isinstance(volts, (list, tuple)) or len(volts) != 2
                or not all(isinstance(v, (int, float)) and 0 < v <= 24
                           for v in volts) or volts[0] > volts[1]):
            fail(where + " voltage_v must be [lo, hi] in (0, 24] V")
        if not (isinstance(entry["current_ma"], (int, float))
                and 0 <= entry["current_ma"] < 5000):
            fail(where + " current_ma must be a number in [0, 5000)")
        link = entry["purchase_link"]
        if not (isinstance(link, str) and link.startswith("https://")):
            fail(where + " purchase_link must start with https://")
        elif any(bit in link.lower() for bit in FORBIDDEN_URL_BITS):
            fail(where + " purchase_link looks transactional; search links only")
        for prose in ("name", "purchase_note", "caveats"):
            if not (isinstance(entry[prose], str) and len(entry[prose]) >= 8):
                fail(where + " %s must be a real sentence-ish string" % prose)

    if len(kinds_seen) < MIN_KINDS:
        fail("need at least %d distinct kinds, found %s"
             % (MIN_KINDS, sorted(kinds_seen)))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "service")
    if fail.count:
        sys.exit(1)
    print("OK: motor component entries pass the gate")
