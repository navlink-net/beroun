#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Makes apps/babylon-hello/gem.glb: a faceted octahedron-based gem (flat normals, one gold PBR material), by hand, so the example's model has a known source and no tool is needed.
    scripts/make-example-glb.py [OUT]       default apps/babylon-hello/gem.glb"""
import json, os, struct, sys

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "apps", "babylon-hello", "gem.glb")
# a crown and a pavilion: 8 corners of a square girdle (radius 1 and 0.8, rotated 22.5 deg), a table on top, a point below
import math
gird = [(math.cos(math.radians(45 * i)), 0.0, math.sin(math.radians(45 * i))) for i in range(8)]
tab = [(0.55 * math.cos(math.radians(45 * i + 22.5)), 0.45, 0.55 * math.sin(math.radians(45 * i + 22.5))) for i in range(8)]
tri = []
for i in range(8):
    j = (i + 1) % 8
    tri.append((tab[i], gird[i], gird[j]))     # crown, sloping out
    tri.append((tab[i], gird[j], tab[j]))      # crown, between the table's corners
    tri.append((gird[j], gird[i], (0.0, -1.1, 0.0)))   # pavilion
tri.append(None)   # the table: a fan from the middle
fan = [(tab[j], tab[i], (0.0, 0.45, 0.0)) for i in range(8) for j in [(i + 1) % 8]]
tri = [t for t in tri if t] + fan
pos, nor, idx = [], [], []
for a, b, c in tri:
    u = [b[k] - a[k] for k in range(3)]; v = [c[k] - a[k] for k in range(3)]
    n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
    l = math.sqrt(sum(x * x for x in n)) or 1.0
    n = [x / l for x in n]
    # glTF faces are counter-clockwise seen from outside: flip a triangle whose normal points to the middle
    cx = [(a[k] + b[k] + c[k]) / 3 for k in range(3)]
    if sum(n[k] * cx[k] for k in range(3)) < 0:
        b, c = c, b; n = [-x for x in n]
    base = len(pos)
    for p in (a, b, c):
        pos.append(p); nor.append(n)
    idx += [base, base + 1, base + 2]
pb = b"".join(struct.pack("<3f", *p) for p in pos)
nb = b"".join(struct.pack("<3f", *n) for n in nor)
ib = struct.pack(f"<{len(idx)}H", *idx)
ib += b"\0" * (-len(ib) % 4)
blob = pb + nb + ib
mn = [min(p[k] for p in pos) for k in range(3)]; mx = [max(p[k] for p in pos) for k in range(3)]
gltf = {
    "asset": {"version": "2.0", "generator": "scripts/make-example-glb.py"},
    "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "gem"}],
    "meshes": [{"name": "gem", "primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2, "material": 0}]}],
    "materials": [{"name": "gold", "pbrMetallicRoughness": {"baseColorFactor": [1.0, 0.72, 0.16, 1.0], "metallicFactor": 0.35, "roughnessFactor": 0.35}}],
    "accessors": [
        {"bufferView": 0, "componentType": 5126, "count": len(pos), "type": "VEC3", "min": mn, "max": mx},
        {"bufferView": 1, "componentType": 5126, "count": len(nor), "type": "VEC3"},
        {"bufferView": 2, "componentType": 5123, "count": len(idx), "type": "SCALAR"}],
    "bufferViews": [
        {"buffer": 0, "byteOffset": 0, "byteLength": len(pb), "target": 34962},
        {"buffer": 0, "byteOffset": len(pb), "byteLength": len(nb), "target": 34962},
        {"buffer": 0, "byteOffset": len(pb) + len(nb), "byteLength": len(idx) * 2, "target": 34963}],
    "buffers": [{"byteLength": len(blob)}],
}
js = json.dumps(gltf, separators=(",", ":")).encode()
js += b" " * (-len(js) % 4)
glb = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(blob)) + struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(blob), b"BIN\0") + blob
os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
open(OUT, "wb").write(glb)
print(f"{OUT}: {len(glb)} bytes, {len(tri)} triangles")
