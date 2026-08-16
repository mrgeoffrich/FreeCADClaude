#!/usr/bin/env python3
"""Phase 1 spike -- the correlation comparison (stdlib only).

Loads, per shape, the FreeCAD-side JSON (exact per-face CenterOfMass from
Shape.Faces, in Shape.Faces order) and the occt-import-js-side JSON
(approximate per-face centroids from `brep_faces` triangle ranges, in
brep_faces order) and checks:

    for every i: is brep_faces[i]'s centroid nearest to Shape.Faces[i]'s
    centroid -- nearest to THAT ONE specifically, not just close to any?

`exact_order_match` is true ONLY when the nearest-match assignment recovered
is literally the identity permutation for every face. Any face whose nearest
match is a DIFFERENT index than its own is a mismatch, listed with both
indices, both centroids and the distance. Nothing is averaged away.

Usage: python3 compare.py [shape...]   (default: both shapes)
"""

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "out")
SHAPES = ["box_with_hole", "filleted_box"]


def dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def load(path):
    with open(path) as fh:
        return json.load(fh)


def compare_shape(name):
    fc = load(os.path.join(OUT_DIR, name + ".freecad.faces.json"))
    oc = load(os.path.join(OUT_DIR, name + ".occt-import-js.json"))
    n_fc = fc["face_count"]
    n_oc = oc["face_count"]
    print("=" * 78)
    print(f"shape: {name}   FreeCAD faces={n_fc}   occt-import-js faces={n_oc}")
    if n_fc != n_oc:
        print(f"  !! FACE COUNT MISMATCH: FreeCAD {n_fc} vs occt-import-js {n_oc}")
        return False, []
    fc_faces = {f["index"]: f for f in fc["faces"]}
    oc_faces = {f["index"]: f for f in oc["faces"]}
    mismatches = []
    max_identity_dist = 0.0
    for i in range(n_fc):
        c_fc = fc_faces[i]["centroid"]
        # Nearest occt-import-js centroid to THIS FreeCAD face's centroid.
        dists = [(j, dist(c_fc, oc_faces[j]["centroid"])) for j in range(n_oc)]
        j_star, d_star = min(dists, key=lambda t: t[1])
        if j_star == i:
            max_identity_dist = max(max_identity_dist, d_star)
            print(
                f"  face {i:2d}: brep_faces[{i}] nearest to Shape.Faces[{i}] "
                f"(d={d_star:.6f} mm)"
            )
        else:
            c_oc = oc_faces[j_star]["centroid"]
            print(
                f"  face {i:2d}: MISMATCH -- brep_faces[{j_star}] is nearest "
                f"(d={d_star:.6f} mm), not brep_faces[{i}]"
            )
            mismatches.append(
                {
                    "freecad_index": i,
                    "occt_ordinal_matched": j_star,
                    "distance": d_star,
                    "note": (
                        f"Shape.Faces[{i}] centroid {c_fc} matched "
                        f"brep_faces[{j_star}] centroid {c_oc}"
                    ),
                }
            )
    exact = len(mismatches) == 0
    print(f"  exact_order_match: {exact}")
    if exact:
        print(f"  nearest-match distance on the identity assignment (max): {max_identity_dist:.6f} mm")
    return exact, mismatches


def main():
    shapes = sys.argv[1:] or SHAPES
    results = {}
    for name in shapes:
        exact, mismatches = compare_shape(name)
        results[name] = {"exact_order_match": exact, "mismatches": mismatches}
    print("=" * 78)
    overall = all(r["exact_order_match"] for r in results.values())
    for name, r in results.items():
        print(f"  {name}: exact_order_match={r['exact_order_match']}")
    print(f"  overall_correlation_holds: {overall}")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
