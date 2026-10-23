"""Reading what a binary glTF says about itself, without its buffers.

A `.glb` is a 12-byte header, then a JSON chunk, then (usually) a binary chunk holding every
vertex. Registration needs only the first two: that the file is a glTF 2.0 container at all,
and the box its geometry occupies. The box is in the JSON already -- glTF requires `min` and
`max` on every POSITION accessor -- so a model of hundreds of megabytes is checked by reading
a few kilobytes of it.
"""

import json
import math
import struct
from typing import Callable

#: The header: magic, version, total length.
HEADER_BYTES = 12
#: A chunk's own header: its length and its type.
CHUNK_HEADER_BYTES = 8
_MAGIC = b"glTF"
_JSON_CHUNK = 0x4E4F534A

Matrix = list[list[float]]
Bounds = dict[str, list[float]]


def read_glb_json(read: Callable[[int, int], bytes], *, where: str) -> dict:
    """The JSON chunk of a binary glTF, read through ``read(start, length)``.

    Refuses anything that is not a glTF 2.0 container in so many words: a `.gltf` (JSON with
    external buffers) has no single object to address, and a glTF 1.0 file has none of the
    accessor bounds this module reads.
    """
    head = read(0, HEADER_BYTES + CHUNK_HEADER_BYTES)
    if len(head) < HEADER_BYTES + CHUNK_HEADER_BYTES or head[:4] != _MAGIC:
        raise ValueError(f"{where} is not a binary glTF: it does not start with the `glTF` magic. Export the model as a `.glb` -- a `.gltf` with separate buffers is several files, and a CAD model is one.")
    version, total = struct.unpack_from("<II", head, 4)
    if version != 2:
        raise ValueError(f"{where} is a glTF {version}.0 file. Only glTF 2.0 is read: re-export it from a current exporter.")
    chunk_length, chunk_type = struct.unpack_from("<II", head, HEADER_BYTES)
    if chunk_type != _JSON_CHUNK:
        raise ValueError(f"{where} does not begin with a JSON chunk, which glTF 2.0 requires. The file is damaged or was not written by a glTF exporter.")
    if HEADER_BYTES + CHUNK_HEADER_BYTES + chunk_length > total:
        raise ValueError(f"{where} declares a JSON chunk of {chunk_length} bytes, longer than the {total} the file says it holds. The upload is truncated or the file is damaged.")
    raw = read(HEADER_BYTES + CHUNK_HEADER_BYTES, chunk_length)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{where} has a JSON chunk that does not parse: {exc}.") from exc


def _identity() -> Matrix:
    return [[1.0 if row == column else 0.0 for column in range(4)] for row in range(4)]


def _multiply(a: Matrix, b: Matrix) -> Matrix:
    return [[sum(a[row][k] * b[k][column] for k in range(4)) for column in range(4)] for row in range(4)]


def _node_matrix(node: dict) -> Matrix:
    """A node's local transform: its `matrix` (column-major), or its translation · rotation · scale."""
    if "matrix" in node:
        m = node["matrix"]
        return [[float(m[column * 4 + row]) for column in range(4)] for row in range(4)]
    tx, ty, tz = node.get("translation", [0.0, 0.0, 0.0])
    qx, qy, qz, qw = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
    sx, sy, sz = node.get("scale", [1.0, 1.0, 1.0])
    rotation = [
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
        [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
        [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)],
    ]
    scale = (sx, sy, sz)
    out = _identity()
    for row in range(3):
        for column in range(3):
            out[row][column] = rotation[row][column] * scale[column]
    out[0][3], out[1][3], out[2][3] = tx, ty, tz
    return out


def scene_bounds(document: dict) -> Bounds | None:
    """The box the default scene's geometry occupies, in the file's own coordinates, or None.

    Each primitive's POSITION accessor box is carried through its node's world transform --
    corner by corner, so a rotated part is boxed where it is drawn rather than where its
    vertices were written. None when nothing in the scene declares a box: glTF requires the
    bounds, but an exporter that skips them has said nothing this can repeat.
    """
    nodes = document.get("nodes", [])
    meshes = document.get("meshes", [])
    accessors = document.get("accessors", [])
    scenes = document.get("scenes", [])
    if scenes:
        roots = scenes[document.get("scene", 0)].get("nodes", [])
    else:
        # No scene: every node no other node names as a child is a root.
        children = {child for node in nodes for child in node.get("children", [])}
        roots = [index for index in range(len(nodes)) if index not in children]

    low = [math.inf] * 3
    high = [-math.inf] * 3
    stack: list[tuple[int, Matrix]] = [(root, _identity()) for root in roots]
    seen: set[int] = set()
    while stack:
        index, parent = stack.pop()
        if index in seen or index >= len(nodes):
            continue
        seen.add(index)
        node = nodes[index]
        world = _multiply(parent, _node_matrix(node))
        mesh_index = node.get("mesh")
        if mesh_index is not None and mesh_index < len(meshes):
            for primitive in meshes[mesh_index].get("primitives", []):
                position = primitive.get("attributes", {}).get("POSITION")
                if position is None or position >= len(accessors):
                    continue
                accessor = accessors[position]
                if "min" not in accessor or "max" not in accessor:
                    continue
                lo, hi = accessor["min"], accessor["max"]
                for corner in range(8):
                    point = [hi[axis] if corner >> axis & 1 else lo[axis] for axis in range(3)]
                    for axis in range(3):
                        value = sum(world[axis][k] * point[k] for k in range(3)) + world[axis][3]
                        low[axis] = min(low[axis], value)
                        high[axis] = max(high[axis], value)
        stack.extend((child, world) for child in node.get("children", []))

    if not all(math.isfinite(value) for value in (*low, *high)):
        return None
    return {"min": low, "max": high}
