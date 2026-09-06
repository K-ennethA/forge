"""Minimal .glb reader - just enough to report vertex/triangle counts.

The contract wants ``stats: {verts, faces}`` on a finished job and the whole
service is stdlib-only, so rather than pull in trimesh we parse the glTF JSON
chunk out of the binary container and add up the accessor counts.  A .glb is:

    magic 'glTF' | version u32 | total length u32
    then chunks of: length u32 | type u32 ('JSON' or 'BIN\\0') | payload

Only the JSON chunk is needed; accessor ``count`` fields carry the numbers.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

_MAGIC = b"glTF"
_CHUNK_JSON = 0x4E4F534A  # 'JSON'


class GlbError(ValueError):
    pass


def read_json_chunk(path) -> dict:
    path = Path(path)
    with open(path, "rb") as fh:
        header = fh.read(12)
        if len(header) < 12 or header[:4] != _MAGIC:
            raise GlbError(f"{path} is not a binary glTF file")
        _, _, total = struct.unpack("<4sII", header)

        read = 12
        while read + 8 <= total:
            raw = fh.read(8)
            if len(raw) < 8:
                break
            length, kind = struct.unpack("<II", raw)
            payload = fh.read(length)
            read += 8 + length
            if kind == _CHUNK_JSON:
                return json.loads(payload.decode("utf-8"))
    raise GlbError(f"{path} has no JSON chunk")


def stats(path) -> dict:
    """Return ``{"verts": int, "faces": int, "meshes": int, "materials": int}``.

    Counts are summed across every primitive.  Triangles come from the index
    accessor when there is one, otherwise from the position count (non-indexed
    triangle soup).  Only TRIANGLES-mode primitives contribute faces.
    """
    doc = read_json_chunk(path)
    accessors = doc.get("accessors") or []
    meshes = doc.get("meshes") or []

    def count_of(index):
        try:
            return int(accessors[index].get("count", 0))
        except (IndexError, TypeError, ValueError):
            return 0

    verts = 0
    faces = 0
    prims = 0
    for mesh in meshes:
        for prim in mesh.get("primitives") or []:
            prims += 1
            mode = prim.get("mode", 4)  # 4 == TRIANGLES, the glTF default
            pos = (prim.get("attributes") or {}).get("POSITION")
            n_pos = count_of(pos) if pos is not None else 0
            verts += n_pos
            if mode != 4:
                continue
            if "indices" in prim and prim["indices"] is not None:
                faces += count_of(prim["indices"]) // 3
            else:
                faces += n_pos // 3

    return {
        "verts": verts,
        "faces": faces,
        "meshes": len(meshes),
        "primitives": prims,
        "materials": len(doc.get("materials") or []),
        "images": len(doc.get("images") or []),
    }
