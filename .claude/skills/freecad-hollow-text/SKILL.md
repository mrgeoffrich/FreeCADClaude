---
name: freecad-hollow-text
description: >-
  Turns font text (via Draft ShapeString) into hollow/channel outline
  lettering in this addon's live FreeCAD document -- backlit "channel letter"
  signage, LED-strip nameplates, or any 3D-printed text that needs a thin wall
  around the letterforms instead of solid fill. Exists because the naive
  approach (extrude solid letters, then shrink the wall inward with a 2D
  offset or makeThickness) is a confirmed crash: on cursive/script fonts with
  strokes thinner than the wall thickness, the inward shrink self-intersects
  and can segfault deep inside OCCT's offset code, taking the whole FreeCAD
  process down with any unsaved work. This skill's technique grows the wall
  OUTWARD instead (the robust direction) and hollows per touching
  letter-cluster rather than per glyph, so letters that visually connect
  (cursive joins, italic ligatures) share one continuous channel instead of
  getting pinched shut where they meet. EXPLICIT INVOCATION ONLY: invoke this
  skill only when the user (or a direct instruction earlier in the prompt)
  explicitly asks for it by name -- e.g. via the /freecad-hollow-text slash
  command in the FreeCAD chat panel, or an explicit "use the hollow-text
  skill" request. Do NOT trigger this on topic-matching alone (a request for
  text, a sign, or a nameplate is not, by itself, a reason to invoke it) --
  wait for the explicit ask. Assumes the run_python execution contract from
  the system prompt (pre-bound names, one-call-one-transaction, Quantity).
---

# FreeCAD Hollow Text (Channel Letters)

You turn a text string into hollow outline lettering in the live document —
a thin wall tracing each letterform, open through its depth, sized so an LED
strip or wire can thread through it. This is a narrow, specific recipe within
ordinary run_python scripting: the system prompt's execution contract applies
(pre-bound `doc`/`App`/`Part`/`Draft`, one-call-one-transaction, `inspect_api`,
Quantity gotcha), just one particular geometry technique that's easy to get
catastrophically wrong.

**Scope check first:** if the user just wants solid extruded text (a raised
logo, an embossed label), none of this applies — that's a plain
`Draft.make_shapestring` + `.extrude()`, no hollowing, no special care needed.
This skill only earns its complexity when the letters need to actually be
hollow.

## Why the naive approach crashes

The obvious way to hollow text is: extrude the (possibly multi-letter, fused)
solid letterforms, then remove material from the *inside* — either
`makeThickness` with a negative offset, or a 2D profile shrunk inward via
`makeOffset2D(-wall, ...)` before extruding. Both shrink the letter's own
boundary toward its interior.

That works fine on blocky sans-serif strokes with room to spare. It falls
apart on script/cursive fonts (or any font where wall thickness approaches
stroke width): shrinking a thin stroke inward by more than half its width
has nowhere to go, so the offset geometry self-intersects. When that happens
with a 2D profile offset, it isn't always a clean Python exception — OCCT's
offset algorithm (`BRepOffsetAPI_MakeOffset` → `BRepFill_OffsetWire` →
`BRepMAT2d_BisectingLocus`, the medial-axis machinery it uses to build offset
arcs at corners) can hit a null-pointer dereference and segfault the entire
FreeCAD process. This has been confirmed firsthand: a `SIGSEGV` in exactly
that call chain, triggered by `makeOffset2D(-1.8, join=1, ...)` on a fused
multi-letter cursive profile. `run_python`'s `try/except` cannot catch this —
the process dies before Python's exception machinery ever runs, taking any
unsaved document state with it.

The fix isn't a smaller offset or a different join type — it's to never
shrink inward at all.

## The technique

1. **Build the text.** `Draft.make_shapestring(String=..., FontFile=...,
   Size=...)`, then scale via `.Size` to hit a target width/height (measure
   `Shape.BoundBox` before and after, same as any ShapeString sizing).

2. **Extract per-glyph faces** from `shapestring.Shape.Faces` — one face per
   glyph piece (a dotted "i" is two pieces; some scripts merge adjacent
   letters into one piece already).

3. **Group touching glyphs into clusters before any hollowing.** Cursive and
   italic fonts often have letters that physically touch or overlap (ligature
   joins, connecting swashes). Detect this with pairwise `distToShape` (near-
   zero distance = touching) and union-find the groups. This matters because
   the *next* step hollows per cluster, not per glyph — if you hollowed each
   glyph independently and only fused the resulting solids afterward, one
   letter's outward-grown wall can intrude into a neighboring letter's cavity
   right at the join, blocking the channel exactly where it should stay open.

4. **Merge each cluster's raw (un-offset) glyph faces into one profile.**
   Plain `face.fuse(other_faces)` + `.removeSplitter()` — fusing un-offset
   outlines is a well-behaved boolean op, not the fragile step.

5. **Hollow the merged cluster profile by growing outward, never shrinking:**
   - The cavity is the merged profile itself, untouched — no offset needed on
     it at all. LEDs thread through the letter's natural stroke shape, and any
     enclosed counter (see step 6) stays solid because it's already a hole in
     this same profile.
   - The wall is a *second*, larger profile, built in two steps (step 6
     explains why the outer wire is used on its own):
     1. Offset the cluster's **outer wire only** —
        `Part.Face(cluster_face.OuterWire)`, dropping any inner hole wires —
        outward by `+wall` via `makeOffset2D(wall, join=0, fill=True,
        openResult=False, intersection=False)`. Positive/outward offsets are
        the robust direction in OCCT: convex growth at concave corners
        diverges instead of self-intersecting.
     2. **Fuse that offset result back onto the outer-wire-only face**
        (`.fuse()` + `.removeSplitter()`) before extruding. `fill=True`'s
        docstring reads "the output is a face filling the space covered by
        offset," which reads like it hands back the whole grown blob
        (original + margin) — it doesn't. It's only the thin swept border
        strip *between* the original boundary and the offset boundary
        (confirmed empirically: an outer face of area 262mm² offset by
        1.8mm produces a strip of area ~200mm², not ~280mm²; area scales
        roughly linearly with the offset distance, consistent with
        perimeter × offset, not blob growth). Skip this fuse and the wall
        silently loses all its interior fill — harmless for a holeless
        letter (see step 6) but wrong for any letter with a counter.
   - Extrude both profiles to the target height, `outer_solid.cut(inner_solid)`
     → one hollow "tube" per cluster, possibly plus separate solid "islands"
     for any filled counters (see step 6) — not necessarily one solid.
   - Try a wall-thickness candidate list from thickest to thinnest (e.g.
     `[1.8, 1.2, 0.8, 0.5]`) and take the first that yields a valid solid — a
     script font's thinnest cluster may need a smaller wall than a blockier
     one, and there's no way to know which without trying. Don't require
     exactly one solid (`len(Solids) == 1`); see step 6 for why.

6. **Enclosed counters (e.g. a looped "e" or "o") stay filled solid, as
   separate islands.** A font's own enclosed counter is a *hole* in the glyph
   face. Two things follow from that:
   - **The outer wall offset must ignore it.** Offsetting a face-with-a-hole
     outward also shrinks the hole inward — resurrecting the exact "no room
     to shrink" failure this whole approach exists to avoid, just at the hole
     boundary instead of the outer one. That's why step 5's wall offset
     starts from `Part.Face(cluster_face.OuterWire)` — outer wire only, hole
     dropped — rather than the cluster face itself.
   - **But the counter must still end up filled**, not swallowed into the
     open cavity. Because the outer-wire-only face treats the counter's
     footprint as solid, and step 5's fuse-back-on step (not the bare offset
     strip alone) preserves that fill, the counter's footprint ends up with
     material in `outer_solid` but *not* in `inner_solid` (which extrudes the
     real face, hole intact) — so `outer_solid.cut(inner_solid)` leaves it
     standing. Skip the fuse-back-on step (i.e. use the bare offset strip as
     `outer_solid`, as an earlier version of this skill did) and this
     silently breaks: the strip only traces the outer boundary and never
     covers the counter's interior, so the cut leaves nothing there and the
     counter becomes part of the open cavity — a visibly hollow ring where a
     filled loop was expected. This only shows up from an angled/shaded 3D
     view; a flat top-down orthographic capture hides it — verify hollow
     text with `capture_view` from an angle, not just from directly above.
   - **Heads-up for 3D printing:** a filled counter typically ends up as its
     own separate solid, not fused to the surrounding wall — most fonts don't
     give the counter island and the wall a shared boundary edge to weld
     along (confirmed on Pacifico "Juliette" at 120mm wide/8mm deep: filling
     all counters solid took the final assembly from 2 solids to 9). That's
     fine for viewing, but a floating island isn't anchored to the channel
     walls — flag it to the user so they can plan support (e.g. resting the
     piece on a baseplate) if printing. Don't auto-fuse the islands onto the
     wall to "solve" this; that's a separate, unrequested feature.

7. **Bridge clusters that don't touch at all** (e.g. a genuinely separate
   word, or a stray "i" dot far from its stem) with a plain **solid** web —
   an extruded rectangle between the two clusters' closest points, margin
   extended into each side so it truly overlaps rather than just touching.
   These bridges are never hollowed; they're small structural connectors, and
   a straight extrusion is not at risk of the crash above.

8. **Fuse everything — hollow cluster tubes + solid bridges — into one final
   solid.** `addObject("Part::Feature", name)`, assign `.Shape`, `doc.recompute()`.

Full adaptable code for steps 2–8 is in `references/technique.md` — read it
before writing the actual call(s); don't reimplement this from scratch.

## Sizing the run_python call(s)

The system prompt's "one call is one rollback unit, keep it under about a
second" rule applies here too:

- **Short word/name (a few clusters):** fine as one `run_python` call,
  start to finish — this is what's been validated.
- **Longer text or many disjoint clusters:** split into one call per cluster
  (each committing its hollow tube as its own object), then a final call
  that fuses everything + the bridges. A failure partway through then doesn't
  roll back clusters that already succeeded.

## Other gotchas

- **Locate the font file with `Glob`** (e.g. `**/*Pacifico*`), searching the
  OS's standard font directories (`~/Library/Fonts`, `/Library/Fonts`,
  `/System/Library/Fonts` on macOS; `C:\Windows\Fonts` on Windows) — not
  `Grep`/ripgrep. Content-searching a fonts directory with ripgrep times out
  scanning binary font files; this has happened firsthand and wasted 20
  seconds before falling back to the right approach.
- **Never call `App.newDocument()`** — use the pre-bound `doc`, per the
  execution contract.
- The exterior silhouette ends up `wall` thickness bigger than the true font
  outline (since the wall is added outward, not carved inward) — a minor,
  expected softening of the letterforms, not a bug. Mention it if the user
  seems to expect an exact silhouette match.
- **Verify afterward**, same discipline as any `run_python` step:
  `get_objects`/`get_diagnostics` for a valid shape (one or more solids — more
  than one is expected once any cluster has a filled counter, see step 6), and
  sanity-check that `Volume / (bbox height)` roughly matches the sum of the
  flat end-cap face areas — a segfault-free run doesn't guarantee the geometry
  is actually hollow rather than a solid blob. Also `capture_view` from an
  angled/shaded 3D view, not just a flat top-down one — a wrongly-open counter
  (step 6) is invisible from directly above.
