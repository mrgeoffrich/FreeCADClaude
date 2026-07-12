# PartDesign Body: scripted `newObject` can wire a circular `BaseFeature`

## Symptom

After scripting a new `PartDesign::Fillet` (or any new solid feature) onto a
body via `body.newObject("PartDesign::Fillet", "Fillet")`, the document fails
to recompute with the *previous* tip feature reported invalid — even though
the new feature itself computed fine. Forcing a recompute on the old tip
throws:

```
RuntimeError: The graph must be a DAG.
```

## Root cause

`Body.Group` is just the tree-display order; the actual dependency chain is
carried by each feature's `BaseFeature` property (and `Body.Tip` marks the
current end of that chain). Normally:

```
Pocket001 --BaseFeature--> MirroredTopCut --BaseFeature--> Fillet (Tip)
```

Observed case: the body had a **datum feature** (`PartDesign::Plane`,
`MidPlane`) sitting in `Group` between `Pocket001` and `MirroredTopCut` —
i.e. a non-solid object interleaved with the solid feature spine. When
`body.newObject("PartDesign::Fillet", ...)` inserted the new feature relative
to the *current Tip's position in Group*, it landed **before**
`MirroredTopCut` in the array:

```python
>>> [o.Name for o in body.Group]
['Sketch', 'Pad', 'Sketch001', 'Pocket', 'Sketch002', 'Pocket001',
 'MidPlane', 'Fillet', 'MirroredTopCut']   # Fillet before MirroredTopCut!
```

Some part of Body's chain-maintenance then re-wired `BaseFeature` links to
match that (wrong) Group order, setting:

```
MirroredTopCut.BaseFeature = Fillet     # was Pocket001
Fillet.BaseFeature         = MirroredTopCut   # unchanged, already correct
```

— a two-node cycle. `Fillet` itself computed successfully (it only reads
`MirroredTopCut`'s shape via its `Base` edge references, not via
`BaseFeature`), which is why only the *older* feature showed as invalid.

## Fix

Force `BaseFeature` back to the correct predecessor directly:

```python
doc.MirroredTopCut.BaseFeature = doc.Pocket001
doc.recompute()
```

This is safe — `BaseFeature` is just a property, not something that needs
special API to set — and it immediately cleared the cycle with no other side
effects; `get_diagnostics` reported a clean recompute afterward.

## What did NOT work: fixing the cosmetic Group order

`Group` was still left with `Fillet` listed *before* `MirroredTopCut`, purely
a tree-display ordering artifact (no functional effect — dependency order is
`BaseFeature`/`OutList`, not array position). Attempting to fix this
cosmetic order with the documented API:

```python
body.insertObject(doc.Fillet, doc.MirroredTopCut, True)  # insert after MirroredTopCut
```

**recreated the exact same cycle** (`MirroredTopCut.BaseFeature` flipped back
to `Fillet`) *and* duplicated the `Fillet` entry in `Group`. Had to revert by
re-fixing `BaseFeature` and deduplicating `Group` manually. Conclusion:
`insertObject`'s docstring claim that it "doesn't modify the Tip unlike
addObject()" doesn't mean it's side-effect-free on `BaseFeature` — don't use
it to reorder a body's `Group` after the fact. If the cosmetic order actually
matters to the user, it's safer to fix it from the GUI (drag the tree item)
than to keep guessing at the scripted API.

## Takeaway for `run_python` tool calls

- After using `body.newObject(...)` to add any PartDesign feature onto a body
  that has non-solid datum objects (planes, points, mirror-plane references)
  interleaved in `Group`, **always** call `get_diagnostics` right after and
  check the *previous* tip feature specifically, not just the new one.
- If a cycle appears, fix it by directly reassigning the older feature's
  `BaseFeature` back to its true predecessor (the feature that fed it before
  the new one was inserted) — don't try to fix it via `Group`/`insertObject`
  reordering, which reproduces the bug.
- A `RuntimeError: The graph must be a DAG` from `doc.recompute()` is the
  reliable signal for this specific failure mode.
