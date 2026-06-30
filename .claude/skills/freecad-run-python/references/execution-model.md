# The run_python Execution Model (FreeCAD 1.1)

How code you write here actually gets executed, what's already in scope, how to
recover from errors, and how to verify a step before moving to the next one.
This is harness-specific — not from FreeCAD's docs — and governs every example
in the sibling reference files.

## What's already bound — don't re-import it

Each `run_python` call executes in a namespace FreeCAD's own console doesn't
give you for free:

- **`FreeCAD`, `App`** — the core module (same object, two names).
- **`FreeCADGui`, `Gui`** — present whenever the GUI is up (it always is here).
- **`Part`, `Sketcher`, `PartDesign`, `Draft`** — pre-imported.
- **`doc`** — `FreeCAD.ActiveDocument`, or a freshly created document if none
  existed yet. Never call `FreeCAD.newDocument()` yourself unless you
  deliberately want a *second* document.

Anything else — `Mesh`, `TechDraw`, `math`, `BOPTools`, etc. — needs its own
`import` inside the code you send, same as any script.

**No package installs, ever.** This addon installs zero Python dependencies
of its own (see `install_deps.ps1` — it only checks the `claude` CLI is on
PATH). Whatever ships inside the user's FreeCAD install is the entire
environment: FreeCAD's bundled modules plus the stdlib. Never write code that
assumes `pip install` happened.

## One call = one transaction

Every `run_python` call is wrapped in a single `openTransaction`/`commitTransaction`:

- On success: `doc.recompute()` runs, the transaction commits, the view
  re-fits (`ViewFit`).
- On *any* exception: the transaction aborts, and as a safety net any object
  created since the call started is also explicitly removed (covers the case
  where undo is disabled and abort alone wouldn't roll back). You get the
  full Python traceback back, plus any `stdout` printed before the failure.
  The document is left exactly as it was before the call — fix the code and
  resend, you're never debugging into a half-built mess.
- **A failed recompute does not raise.** PartDesign/Sketcher features can
  silently mark themselves `Invalid`/`Error` (the red tree icons) while the
  call still "succeeds." Don't assume `OK (committed).` means every feature
  is healthy — see *Verifying as you go* below.

This shapes how to size a call: a call is the unit of rollback, so put one
coherent step in it (e.g. "create the Body and the base sketch", or "add the
Pocket and the fillets") rather than the entire part in one shot. If step 6 of
a 9-step part fails, you want steps 1-5 already committed, not rolled back
with them.

## Returning data

The tool returns whatever you `print()`, plus the repr of a variable named
**`result`** if you set one — both together if both are present. Use `result`
for anything structured you'll want to read back (an object's `.Name`, a
computed dimension, a list of created Labels); use `print` for narration.
There's no other return channel — a bare expression at the end of the script
does nothing.

## inspect_api — look before you guess

A companion read-only tool, no approval needed. Pass `names`: a list of
dotted paths to resolve in the same namespace `run_python` would see
(`FreeCAD`, `Part`, `Sketcher`, `PartDesign`, `Draft`, `Gui`, `doc`, and any
of the active document's objects, e.g. `doc.Sketch.addGeometry`). For each it
returns the type, a Python signature when introspectable, the docstring
(FreeCAD's C++-backed methods usually spell out accepted argument forms
there, since plain `help()` often can't), and the public member list for
modules/classes.

Use it **before** writing a call into unfamiliar territory — a Hole feature's
full property set, an unfamiliar `Sketcher.Constraint` overload, whether a
Loft property is called `Sections` or something else this version. Batch
everything you're unsure about into one `inspect_api` call, then write the
code — don't ping-pong one name at a time. The sibling reference files
(`sketcher-scripting.md`, `partdesign-scripting.md`, `part-draft-recipes.md`)
cover the common, verified shapes; reach for `inspect_api` for anything they
don't cover or where the live install might differ.

## Verifying as you go

Because a clean commit doesn't guarantee a healthy feature, and because
sizing calls small means you're checking in often, lean on the read-only
tools between `run_python` calls instead of stacking blind steps:

- **`get_objects`** — every object's name/label/type/position/key dimensions,
  plus `"invalid": true` on anything whose last recompute failed. Call it
  before referencing existing geometry by name, and after any step you're
  not certain about.
- **`get_diagnostics`** — the full detail (name, type, state) for every
  currently-invalid object, when `get_objects`' one-line flag isn't enough to
  diagnose. FreeCAD's own console warning text isn't capturable, so this
  reports *which* features failed, not the raw warning string — go inspect
  that feature's inputs.
- **`view_sketch_svg`** — exact vector view of a sketch, or an orthographic
  `TechDraw` projection of a 3D object (`view=front/top/...`). Prefer this
  over `capture_view` for actually judging geometry — it's exact, not a
  screenshot.
- **`capture_view`** — raster screenshot, useful for a quick "does this look
  roughly right" but not for precise judgment.

A reasonable rhythm: one `run_python` step → `get_objects` (or
`view_sketch_svg` for the sketch you just built) → next step. Don't write
five PartDesign features blind and hope.

## Quantity vs plain numbers

Most dimensional properties (`Length`, `Radius`, `Angle`, ...) are typed
`App::PropertyLength`/`PropertyAngle`/etc. under the hood:

- **Writing**: a plain `float`/`int` is accepted and treated as millimetres
  (or degrees for angles) — `pad.Length = 10` is fine.
- **Reading back**: the property returns a `FreeCAD.Units.Quantity`, not a
  float. `str(q)` gives `"10.0 mm"`. For arithmetic or comparisons, use
  `.Value` (`pad.Length.Value`), not the Quantity itself.
- For literal unit strings/conversions: `FreeCAD.Units.Quantity("1 in").getValueAs("mm")`.

## GUI-thread constraints

The code runs on FreeCAD's GUI thread (that's what makes `doc`,
`FreeCADGui.ActiveDocument`, etc. usable at all) — but that also means:

- **Never block it.** No `time.sleep()` loops, no modal `QDialog.exec_()`,
  no waiting on user input from inside the script — the whole app freezes
  until the call returns, and the harness itself is waiting on this call to
  finish before it can do anything else.
- Keep a single call's work bounded — it should run to completion in well
  under a second of wall-clock CAD work, not minutes.

## Deliberately out of scope here

The global, general-purpose FreeCAD scripting knowledge (custom Workbenches,
`GuiCommand` registration, Coin3D/Pivy scenegraph manipulation, PySide
dialogs/Task panels, persisted macro files, custom `FeaturePython`
ViewProvider classes meant to survive across sessions) doesn't apply to this
addon. `run_python` is the *only* mutation path FreeCAD-side, it's one
confirm-gated script per call against an already-open document — there's no
mechanism here for registering a toolbar command, adding a custom workbench,
or shipping a `.FCMacro` file. If a request genuinely needs one of those
(e.g. "add a permanent toolbar button"), say so plainly rather than trying to
force it through `run_python` — that's outside what this addon can persist.

## Relationship to the other skills

`freecad-design-advisor` produces the *plan* — named features in GUI
language, with real dimensions once the user has answered the clarifying
questions. This skill is the *build* step: turn that plan into actual
`run_python` calls. `core-concepts.md` in that sibling skill already covers
the parametric mental model (Body = one contiguous solid, Tip, datums, the
topological-naming problem, model intent) — that reasoning still applies
here, just expressed in code instead of clicks; it isn't repeated in these
scripting references.
