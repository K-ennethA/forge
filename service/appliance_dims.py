"""``appliance_dims`` -- how big the thing behind the label actually is.

A floor plan says ``washer/dryer`` and draws a rectangle.  The rectangle is the
artist's guess at where it goes; this table is the world's answer to how big it
is.  Same provenance spirit as :mod:`service.components`: every number is a
real, buyable size with one sentence saying what the number *is*, so nobody has
to wonder whether 686 mm was measured or invented.

What this module is for
-----------------------
:func:`lookup` turns a hand-written key label into a size.  It is deliberately
forgiving about spelling -- a plan key says ``W/D``, ``washing machine``,
``stove``, ``queen bed``, and all four mean something specific -- and
deliberately honest about how it got there: every hit carries a ``confidence``
and a ``how`` saying whether the label matched exactly, matched a known alias,
or was fuzzy-matched by :mod:`difflib`.

**An unknown label is ``None``, never a guess.**  The caller (``floorplan``)
then uses the footprint the artist actually drew, which is the right answer:
the drawing is evidence and this table is only a default.

The law about footprints
------------------------
This table never overrides the plan.  A drawn footprint is what the artist
measured (or wants), so ``floorplan`` uses the drawn width and depth and takes
only the **height** from here -- a floor plan is top-down and has no height in
it at all.  The matched size still travels with the spec as
``suggested_size_mm`` so the echo-back drawing can say "you drew 500 x 600; a
washer/dryer pair is usually 1372 x 813" and let the artist decide.

Units are millimetres, always, as ``(width, depth, height)`` with the appliance
facing +Y is its depth axis.  Where a number came from an inch size it is
stated in inches in the note, because that is the number on the box in a US
appliance aisle and rounding it back is how you check this table.
"""

from __future__ import annotations

import copy
import difflib
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: Below this :mod:`difflib` ratio a label is treated as unknown rather than
#: forced onto the nearest entry.  0.72 is set where "washer" still finds
#: ``washer`` through a typo and "xyzzy" finds nothing.
FUZZY_CUTOFF = 0.72

#: Confidence reported for the three non-exact routes in.  An alias is a name
#: somebody deliberately wrote down here, so it is nearly as good as the
#: canonical spelling; a punctuation-squashed hit and a token-subset hit are a
#: step further out; a fuzzy hit reports its own ratio.
ALIAS_CONFIDENCE = 0.95
SQUASHED_CONFIDENCE = 0.92
TOKEN_CONFIDENCE = 0.85

CATEGORIES: Tuple[str, ...] = ("laundry", "kitchen", "bath", "bedroom", "living", "utility")


def _entry(size_mm: Tuple[float, float, float], category: str, note: str,
           aliases: Sequence[str] = ()) -> Dict[str, Any]:
    return {"size_mm": tuple(float(v) for v in size_mm), "category": category,
            "note": note, "aliases": tuple(aliases)}


#: The table.  Keys are the canonical label; ``size_mm`` is width x depth x
#: height with the unit facing you, so ``depth`` is how far it sticks out from
#: the wall it stands against.
APPLIANCES: Dict[str, Dict[str, Any]] = {
    # ---------------------------------------------------------------- laundry
    "washer": _entry(
        (686.0, 813.0, 965.0), "laundry",
        "US standard 27 in washer: 32 in deep, 38 in tall to the lid",
        ("washing machine", "clothes washer", "laundry washer", "wash machine"),
    ),
    "dryer": _entry(
        (686.0, 813.0, 965.0), "laundry",
        "matching 27 in dryer -- same footprint as the washer beside it, by design",
        ("clothes dryer", "tumble dryer", "drier"),
    ),
    "washer/dryer": _entry(
        (1372.0, 813.0, 965.0), "laundry",
        "the pair side by side: two 27 in units touching, so 54 in of wall",
        ("w/d", "wd", "w d", "washer dryer", "washer and dryer", "laundry",
         "laundry pair", "washer + dryer", "wash/dry"),
    ),
    "washer/dryer stacked": _entry(
        (686.0, 813.0, 1880.0), "laundry",
        "the same two units stacked: one 27 in footprint, 74 in to the top control panel",
        ("w/d stacked", "stacked w/d", "stacked washer dryer", "stacked washer/dryer",
         "laundry stack", "stacked laundry", "stackable washer dryer"),
    ),
    "water heater": _entry(
        (508.0, 508.0, 1473.0), "utility",
        "40 gallon tank heater: 20 in across the jacket, 58 in tall before flue",
        ("hot water heater", "water tank", "boiler", "hwh"),
    ),
    # ---------------------------------------------------------------- kitchen
    "fridge": _entry(
        (914.0, 762.0, 1753.0), "kitchen",
        "36 in French-door refrigerator: 30 in deep without handles, 69 in tall",
        ("refrigerator", "icebox", "freezer", "fridge/freezer", "refrigerator freezer",
         "ref", "frig"),
    ),
    "fridge counter depth": _entry(
        (914.0, 610.0, 1753.0), "kitchen",
        "counter-depth refrigerator: 24 in body so the door closes flush with a 25 in counter",
        ("counter depth fridge", "counter-depth fridge", "counter depth refrigerator",
         "cd fridge", "shallow fridge"),
    ),
    "range": _entry(
        (762.0, 660.0, 914.0), "kitchen",
        "30 in freestanding range: 26 in deep, counter height, handle not counted",
        ("stove", "oven", "cooker", "stove/range", "range/oven", "cooktop",
         "stove top", "gas range", "electric range"),
    ),
    "dishwasher": _entry(
        (610.0, 610.0, 864.0), "kitchen",
        "24 in built-in dishwasher -- the slot it slides into under a counter",
        ("dish washer", "dw", "dishwaser"),
    ),
    "kitchen sink": _entry(
        (762.0, 559.0, 229.0), "kitchen",
        "30 in single-bowl drop-in; the height is the BASIN depth, it hangs in the counter",
        ("sink base", "double sink", "kitchen basin"),
    ),
    "counter": _entry(
        (1219.0, 610.0, 914.0), "kitchen",
        "kitchen base cabinet run: 24 in deep, 36 in high -- the WIDTH is whatever "
        "your run is, so draw that one yourself",
        ("countertop", "counters", "base cabinets", "cabinets", "cabinet run", "worktop"),
    ),
    "island": _entry(
        (1829.0, 1016.0, 914.0), "kitchen",
        "72 x 40 in kitchen island at counter height; needs 36 in of aisle all round",
        ("kitchen island", "breakfast bar"),
    ),
    "microwave": _entry(
        (762.0, 406.0, 432.0), "kitchen",
        "30 in over-the-range microwave; a countertop one is nearer 500 x 400 x 300",
        ("micro", "otr microwave", "microwave oven"),
    ),
    # ------------------------------------------------------------------- bath
    "toilet": _entry(
        (508.0, 762.0, 787.0), "bath",
        "two-piece toilet: 20 in wide, 30 in wall to bowl front, 31 in to the tank lid",
        ("wc", "water closet", "commode", "loo", "lav"),
    ),
    "sink vanity": _entry(
        (610.0, 533.0, 864.0), "bath",
        "24 in single bathroom vanity: 21 in deep, 34 in to the counter top",
        ("vanity", "bathroom sink", "bath sink", "sink", "basin", "washbasin",
         "vanity unit"),
    ),
    "bathtub": _entry(
        (1524.0, 762.0, 508.0), "bath",
        "standard alcove tub: 60 x 30 in, 20 in deep -- the one that fits between three walls",
        ("tub", "bath", "bath tub", "soaking tub"),
    ),
    "shower": _entry(
        (914.0, 914.0, 2032.0), "bath",
        "36 in square stall; the 80 in height is the ENCLOSURE, the pan is 4 in of it",
        ("shower stall", "shower enclosure", "stall shower", "walk in shower"),
    ),
    # ---------------------------------------------------------------- bedroom
    "bed twin": _entry(
        (991.0, 1905.0, 635.0), "bedroom",
        "38 x 75 in twin mattress on a frame; the height is the top of the mattress",
        ("twin bed", "single bed", "twin", "twin mattress"),
    ),
    "bed full": _entry(
        (1372.0, 1905.0, 635.0), "bedroom",
        "54 x 75 in full/double mattress -- same length as a twin, 16 in wider",
        ("full bed", "double bed", "full", "double mattress", "bed double"),
    ),
    "bed queen": _entry(
        (1524.0, 2032.0, 635.0), "bedroom",
        "60 x 80 in queen mattress, the common master-bedroom size",
        ("queen bed", "queen", "queen mattress", "bed"),
    ),
    "bed king": _entry(
        (1930.0, 2032.0, 635.0), "bedroom",
        "76 x 80 in king mattress -- two twin XLs side by side, which is what it is",
        ("king bed", "king", "king mattress", "eastern king"),
    ),
    "nightstand": _entry(
        (559.0, 406.0, 610.0), "bedroom",
        "22 x 16 in bedside table at mattress height",
        ("night stand", "bedside table", "bed side table"),
    ),
    "dresser": _entry(
        (1524.0, 508.0, 864.0), "bedroom",
        "60 in six-drawer dresser, 20 in deep -- allow 24 in in front to open a drawer",
        ("chest of drawers", "drawers", "bureau", "chest"),
    ),
    "wardrobe": _entry(
        (1219.0, 610.0, 2032.0), "bedroom",
        "48 in double wardrobe: 24 in deep because that is what a hanger needs",
        ("closet", "armoire", "clothes closet", "wardrobe closet"),
    ),
    # ----------------------------------------------------------------- living
    "sofa": _entry(
        (2134.0, 914.0, 838.0), "living",
        "84 in three-seat sofa: 36 in deep, 33 in to the back cushion",
        ("couch", "settee", "three seat sofa", "3 seat sofa", "lounge"),
    ),
    "desk": _entry(
        (1219.0, 610.0, 749.0), "living",
        "48 x 24 in desk at the 29.5 in standard writing height",
        ("writing desk", "work desk", "table desk", "computer desk", "workstation"),
    ),
    "dining table": _entry(
        (1829.0, 1067.0, 762.0), "living",
        "72 x 42 in six-seat table at 30 in; leave 36 in of chair pull-back all round",
        ("dining room table", "kitchen table", "dinner table", "table"),
    ),
    "bookshelf": _entry(
        (762.0, 305.0, 1829.0), "living",
        "30 in five-shelf bookcase, 12 in deep, 72 in tall",
        ("bookcase", "book shelf", "shelving", "shelves"),
    ),
    "tv": _entry(
        (1445.0, 89.0, 826.0), "living",
        "65 in diagonal flat panel: 57 in wide, 33 in tall, 3.5 in off the wall on a mount",
        ("television", "flat screen", "flatscreen", "telly", "screen"),
    ),
}


# ==========================================================================
# Matching
# ==========================================================================

_SEPARATORS = re.compile(r"[\s_\-,]+")
_SQUASH = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    """Lowercase, one space between words, punctuation other than ``/`` kept."""
    return _SEPARATORS.sub(" ", str(text).strip().lower()).strip()


def _squash(text: str) -> str:
    """Everything but letters and digits removed, so ``W/D`` and ``wd`` agree."""
    return _SQUASH.sub("", str(text).strip().lower())


def _tokens(text: str) -> List[str]:
    return [token for token in _norm(text).split(" ") if token]


def _build_index() -> Dict[str, Tuple[str, str, float]]:
    """Normalised key -> (canonical name, how it matched, confidence).

    Built in three passes so a canonical spelling always beats another entry's
    alias, and an alias always beats a punctuation-squashed collision.
    """
    index: Dict[str, Tuple[str, str, float]] = {}
    for name in APPLIANCES:
        index.setdefault(_norm(name), (name, "exact", 1.0))
    for name, entry in APPLIANCES.items():
        for alias in entry["aliases"]:
            index.setdefault(_norm(alias), (name, "alias", ALIAS_CONFIDENCE))
    for name, entry in APPLIANCES.items():
        index.setdefault(_squash(name), (name, "squashed", SQUASHED_CONFIDENCE))
        for alias in entry["aliases"]:
            index.setdefault(_squash(alias), (name, "squashed", SQUASHED_CONFIDENCE))
    return index


_INDEX: Dict[str, Tuple[str, str, float]] = _build_index()

#: Canonical names with their token sets, longest first, for the subset pass.
#: Longest first is what makes "stacked washer/dryer" land on the stacked entry
#: rather than on the plain pair it also contains.
_TOKEN_CANDIDATES: List[Tuple[int, str, frozenset]] = sorted(
    ((len(_tokens(name)), name, frozenset(_tokens(name))) for name in APPLIANCES),
    key=lambda item: (-item[0], item[1]),
)


def names() -> List[str]:
    """Every canonical label in the table, alphabetically."""
    return sorted(APPLIANCES)


def catalog(category: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """A copy of the table, optionally one category of it."""
    if category is not None and category not in CATEGORIES:
        raise KeyError(
            f"unknown category {category!r}; the table has {', '.join(CATEGORIES)}"
        )
    return {
        name: copy.deepcopy(entry)
        for name, entry in APPLIANCES.items()
        if category is None or entry["category"] == category
    }


def _result(query: str, name: str, how: str, confidence: float) -> Dict[str, Any]:
    entry = APPLIANCES[name]
    width, depth, height = entry["size_mm"]
    return {
        "query": str(query),
        "match": name,
        "how": how,
        "confidence": round(float(confidence), 3),
        "size_mm": [width, depth, height],
        "width_mm": width,
        "depth_mm": depth,
        "height_mm": height,
        "category": entry["category"],
        "note": entry["note"],
    }


def lookup(label: Any, minimum_confidence: float = FUZZY_CUTOFF) -> Optional[Dict[str, Any]]:
    """The real-world size behind a plan label, or ``None`` if we do not know it.

    Five routes in, tried in this order, each less certain than the last:

    1. the canonical spelling (``washer/dryer``)                -> 1.0
    2. a listed alias (``W/D``, ``stove``, ``queen bed``)       -> 0.95
    3. punctuation squashed away (``wd``, ``w-d``)              -> 0.92
    4. the entry's words all appear in the label
       (``washer/dryer here``, ``big queen bed``)               -> 0.85
    5. :mod:`difflib`, above *minimum_confidence*               -> its own ratio

    Returns ``{"query", "match", "how", "confidence", "size_mm",
    "width_mm", "depth_mm", "height_mm", "category", "note"}`` or ``None``.

    ``None`` is a real answer and the caller must handle it: the plan's own
    drawn footprint is then the size, which is exactly right for the sofa
    somebody's grandmother made.
    """
    if not isinstance(label, str) or not label.strip():
        return None

    normalized = _norm(label)
    hit = _INDEX.get(normalized)
    if hit is not None:
        return _result(label, hit[0], hit[1], hit[2])

    squashed = _squash(label)
    if squashed:
        hit = _INDEX.get(squashed)
        if hit is not None:
            return _result(label, hit[0], "squashed" if hit[1] == "exact" else hit[1],
                           min(hit[2], SQUASHED_CONFIDENCE))

    label_tokens = frozenset(_tokens(label))
    if label_tokens:
        for _count, name, tokens in _TOKEN_CANDIDATES:
            if tokens and tokens <= label_tokens:
                return _result(label, name, "tokens", TOKEN_CONFIDENCE)

    cutoff = max(0.0, min(1.0, float(minimum_confidence)))
    close = difflib.get_close_matches(normalized, list(_INDEX), n=1, cutoff=cutoff)
    if close:
        key = close[0]
        ratio = difflib.SequenceMatcher(None, normalized, key).ratio()
        name = _INDEX[key][0]
        return _result(label, name, "fuzzy", ratio)
    return None


def height_for(label: Any, fallback_mm: float) -> Tuple[float, str, Optional[str]]:
    """Height in mm for a label, plus where it came from.

    Returns ``(height_mm, source, matched_name)`` where *source* is
    ``"appliance"`` when this table answered and ``"default"`` when it did not.
    A floor plan is top-down and has no height in it, so this is the one number
    a fixture cannot get from the drawing.
    """
    found = lookup(label)
    if found is None:
        return float(fallback_mm), "default", None
    return float(found["height_mm"]), "appliance", found["match"]


__all__ = [
    "APPLIANCES",
    "CATEGORIES",
    "FUZZY_CUTOFF",
    "catalog",
    "height_for",
    "lookup",
    "names",
]
