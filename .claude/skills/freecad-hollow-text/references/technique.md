# Hollow Text — Adaptable Code Template

This is the validated shape of the algorithm (headless `freecadcmd` runs
against Pacifico "Juliette" at 120mm wide, 8mm deep — zero crashes across
several iterations, confirmed single valid solid, correct height, genuinely
hollow). Adapt names/parameters to the request; don't paste this verbatim
without checking it against what the user actually asked for (text, font,
size, height, wall thickness).

Everything below assumes the pre-bound `run_python` namespace: `doc`, `App`,
`Part`, `Draft` already exist — don't re-import or re-create a document.

## Parameters to pin down before writing the call

- `TEXT` — the string.
- `FONT_PATH` — resolved via `Glob`, not hardcoded (see SKILL.md gotcha).
- Target size — either a `Size` value directly, or a target bounding-box
  width/height to scale to (measure `BoundBox` before/after, same as any
  ShapeString sizing).
- `HEIGHT` — extrusion depth (the LED channel's depth).
- `WALL_CANDIDATES` — thickest-first fallback list, e.g. `[1.8, 1.2, 0.8, 0.5]`.
  Pick the top of the range based on what's structurally reasonable for the
  print/material, not arbitrarily — this is a starting point, not gospel.
- Bridge `width`/`margin` for connecting disjoint clusters (defaults of
  `width=4.0, margin=3.0` worked for a 120mm-wide sign; scale with the sign).

## Step 2–3: build, extract glyphs, cluster

```python
ss = Draft.make_shapestring(String=TEXT, FontFile=FONT_PATH, Size=50)
doc.recompute()
bb = ss.Shape.BoundBox
scale = TARGET_WIDTH / bb.XLength
ss.Size = 50 * scale
doc.recompute()

glyph_faces = list(ss.Shape.Faces)

clusters = []
used = [False] * len(glyph_faces)
for i in range(len(glyph_faces)):
    if used[i]:
        continue
    cluster = [i]
    used[i] = True
    changed = True
    while changed:
        changed = False
        for j in range(len(glyph_faces)):
            if used[j]:
                continue
            if any(glyph_faces[j].distToShape(glyph_faces[k])[0] < 1e-6 for k in cluster):
                cluster.append(j)
                used[j] = True
                changed = True
    clusters.append(cluster)
```

## Step 4: merge each cluster's raw outlines

```python
def merge_cluster_faces(indices):
    faces = [glyph_faces[i] for i in indices]
    if len(faces) == 1:
        return faces[0]
    merged = faces[0].fuse(faces[1:])
    merged = merged.removeSplitter()
    # normally yields exactly 1 face; if not, the cluster has pieces that
    # didn't actually weld into one profile -- fall back to hollowing each
    # of merged.Faces independently rather than forcing a single result
    return merged.Faces[0] if len(merged.Faces) == 1 else merged.Faces
```

## Step 5–6: hollow by growing outward, adaptive wall

```python
def hollow_profile(face, height, wall_candidates):
    """Cavity = face itself (holes/counters excluded, so they stay solid).
    Wall = outer boundary grown outward, fused back onto the full outer
    silhouette (not just the hole-dropping outer_only alone) before cutting
    the cavity -- otherwise makeOffset2D(fill=True) returns only the thin
    swept strip, not a filled blob, and any counter never gets material to
    begin with."""
    outer_only = Part.Face(face.OuterWire)
    for wall in wall_candidates:
        try:
            strip = outer_only.makeOffset2D(wall, join=0, fill=True,
                                             openResult=False, intersection=False)
            grown = outer_only.fuse(strip)
            grown = grown.removeSplitter()
            if len(grown.Faces) != 1:
                continue  # outer boundary split oddly at this wall -- try smaller
            outer_solid = grown.Faces[0].extrude(App.Vector(0, 0, height))
            inner_solid = face.extrude(App.Vector(0, 0, height))
            tube = outer_solid.cut(inner_solid)
            tube = tube.removeSplitter()
            if tube.isValid() and len(tube.Solids) >= 1:
                return tube, wall
        except Exception:
            continue
    return face.extrude(App.Vector(0, 0, height)), None  # fallback: solid, no channel
```

Call `hollow_profile` once per cluster (or once per face, for the rare
cluster that didn't merge into one). A cluster with a filled counter (a
looped "e", "o", etc.) typically returns *more than one* solid — the tube
plus one separate island per counter, since most fonts don't give the
island a shared edge with the wall to weld along; that's expected, not a
failure (see SKILL.md step 6 for the 3D-printing heads-up on floating
islands). If a cluster falls back to solid (no wall in the candidate list
worked at all), say so plainly rather than silently shipping a letter with
no channel — the user may want a smaller minimum candidate, or may be fine
with that one letter solid.

## Step 7: bridge disjoint clusters

```python
def make_bridge_solid(p1, p2, width, height, margin=3.0):
    p1 = App.Vector(p1.x, p1.y, 0)
    p2 = App.Vector(p2.x, p2.y, 0)
    direction = p2 - p1
    direction.normalize()
    p1 = p1 - direction * margin
    p2 = p2 + direction * margin
    perp = App.Vector(-direction.y, direction.x, 0) * (width / 2.0)
    c1, c2, c3, c4 = p1 + perp, p1 - perp, p2 - perp, p2 + perp
    wire = Part.makePolygon([c1, c2, c3, c4, c1])
    return Part.Face(wire).extrude(App.Vector(0, 0, height))

cluster_shapes = [Part.makeCompound([glyph_faces[i] for i in c]) for c in clusters]
bridge_solids = []
for a, b in zip(range(len(cluster_shapes)), range(1, len(cluster_shapes))):
    d, pts, _ = cluster_shapes[a].distToShape(cluster_shapes[b])
    p1, p2 = pts[0]
    bridge_solids.append(make_bridge_solid(p1, p2, width=4.0, height=HEIGHT))
```

This connects clusters in left-to-right sequence (`zip(order, order[1:])`),
which is right for a single line of text. For a multi-line or multi-word
layout, pick bridge pairs deliberately instead — nearest-neighbor by
position, not just list order.

## Step 8: final assembly

```python
all_solids = hollow_results + bridge_solids  # hollow_results from step 5-6, one per cluster
final = all_solids[0].fuse(all_solids[1:])
final = final.removeSplitter()

obj = doc.addObject("Part::Feature", "HollowText")
obj.Shape = final
doc.recompute()

result = {
    "num_solids": len(final.Solids),
    "is_valid": final.isValid(),
    "bbox": (final.BoundBox.XLength, final.BoundBox.YLength, final.BoundBox.ZLength),
}
print(result)
```

`is_valid == True` and `len(final.Solids) >= 1` are the two things worth
checking before calling it done — same as `result` reads back through
`run_python`'s return channel per the system prompt's execution contract.
Don't expect exactly 1: any cluster with a filled counter (SKILL.md step 6)
contributes its own separate solid island on top of the main tube(s), so a
healthy result is often several solids, not one.

## What "good" looks like when verified

From the validated run (Pacifico "Juliette", 120mm wide, 8mm deep, wall
1.2–1.8mm depending on cluster): 9 valid solids (the tube pieces plus one
filled island per enclosed counter — up from 2 solids before counters were
fixed to fill correctly), bbox height matches the extrusion height to within
OCCT's usual tolerance (~0.01mm), and `Volume / height` roughly equals the
sum of the flat end-cap face areas — confirming a genuine constant-
cross-section hollow shape rather than a solid blob or a broken partial
shell. Use `get_objects`/`get_diagnostics` after the call the same way you
would for any `run_python` step, and `capture_view` from an angle (not
straight down) to actually see whether counters came out filled.
