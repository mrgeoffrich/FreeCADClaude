# Device annotation — implementation plan

Companion to `device-annotation-design.md`. Six phases; each one ends with the
addon working and something you can actually try, and each is a commit.

The ordering is by **risk, not by layer**. The three things that could invalidate
the design are all unknowns you can only settle on hardware: does a LAN page load
on both tablets, does the pen feel right, and is the mm/px derivation actually
correct. Phases 0 and 1 answer all three before any tool, schema or MCP wiring
exists to be thrown away.

---

## Phase 0 — Scaffold and LAN spike

**Goal:** a page served by FreeCAD, opened on the iPad and the Samsung, with the
pen drawing *something*. Nothing else.

**Why first:** every later phase assumes LAN reachability, the token flow, and a
committed-build pipeline. All three are cheap to prove and annoying to discover
broken later — corporate/guest wifi with client isolation, or a firewall prompt
FreeCAD never shows, would change the whole approach.

**Changes**

- `web/` Vite + TypeScript + Vitest project. One runtime dep: `perfect-freehand`.
  `vite.config.ts` with `base: "./"`, `outDir: "../freecad/freecadclaude/device_ui"`,
  no code splitting, assets inlined.
- `freecad/freecadclaude/device_server.py`: `start()` / `stop()`, ephemeral port
  on `0.0.0.0`, token, static serving from `device_ui/` with realpath
  containment. No API routes yet.
- Chat panel: a "Device" button that starts the server and shows the URL.
- `.gitignore`: add `web/node_modules/`, `web/dist/`; confirm
  `freecad/freecadclaude/device_ui/` is **not** ignored.
- `deploy.ps1` / `deploy.sh`: add `web` to the exclude lists.

**Tests:** `freecadcmd` script — server starts, unknown token is rejected,
`../` in a path is rejected, a known asset is served.

**Done when:** both devices load the page over wifi, and a pen stroke appears on
a canvas. If a device can't reach it, stop here and fix that before building
anything on top.

---

## Phase 1 — Drawing core

**Goal:** a good pen app with no networking. Load an image from the device, draw,
undo, clear, flatten and download the result.

**Why here:** stroke feel is the one thing that decides whether you use this, and
it is entirely local. Getting it right against a `<input type="file">` image needs
no server, no tool, and no schema — and the device-import path it exercises is a
shipped feature, not scaffolding.

**Changes**

- `src/input.ts` — the pointer policy as a pure `shouldDraw(state, event)`:
  pen-only once a stylus is seen, `touch-action: none`, `getCoalescedEvents()`.
- `src/strokes.ts` — perfect-freehand wrapper, `e.pressure` → width.
- `src/canvas.ts` — render loop at `devicePixelRatio`, base image + strokes, and
  **the view transform** (`{scale, tx, ty}`, identity for now). All coordinates
  are stored in image space and pass through it; this is what makes pinch-zoom a
  contained change later instead of a refactor.
- `src/ui.ts` — the toolbar from the mockup: source, pen, undo, clear.
- Image import: file input for the library, a second with
  `capture="environment"` for the camera. `createImageBitmap(blob,
  {imageOrientation: "from-image"})` — without it, phone photos arrive rotated.
- Flatten to PNG and downscale to a 1568px long edge (used by the download here,
  by the upload in phase 2).

**Tests:** Vitest on `input` (the pen-then-palm event sequence is a unit test, not
a discovery on a tablet) and on the downscale/flatten geometry.

**Done when:** you can mark up a photo on both devices and it feels right. Judge
palm rejection, stroke lag and taper here — a fix costs nothing now and is
entangled with everything by phase 3.

---

## Phase 2 — The round trip

**Goal:** an image goes FreeCAD → device → back, and Claude sees it.

**Why here:** this is the integration risk — MCP wiring, the inline-image return,
session-folder plumbing. Get an image flowing end to end before adding semantics
to it.

**Changes**

- `device_server.py`: `GET /api/latest`, `GET /api/image/<id>`, `POST /api/upload`
  (12 MB cap, PNG/JPEG magic-byte check, generated names via `_artifact_path`),
  `GET /api/events` (SSE, woken by a `threading.Condition`). Plus
  `publish(path, meta)` for the tool to call.
- `freecad_tools/tools_device.py`: `send_to_device` (reuses
  `render._offscreen_shot` / `_capture_setup` / `_apply_camera_plan` — no new
  capture code) and `read_device_image`, returning `(text, png_path)` so
  `gui_bridge` base64s it into an inline image block. Register both in
  `freecad_tools/__init__.py`.
- `send_to_device` returns immediately and tells Claude to ask the user to say
  when they've sent it back — the same two-halves rule as `annotate_view`, for the
  same reason.
- Frontend: `src/api.ts` (token in `sessionStorage`, fetch wrappers, `EventSource`),
  a Send button, and the incoming-capture banner (notify, don't clobber an
  in-progress drawing).
- Chat panel: note "📱 image received" in the transcript on upload.

**Tests:** `freecadcmd` — upload validation (oversize, wrong magic bytes,
traversal in a filename), publish/fetch round trip. A manual end-to-end: ask
Claude to send you a view, scribble on it, ask it what you drew.

**Done when:** Claude can describe a mark you made on the iPad, with the camera
angle and extents context replayed correctly.

---

## Phase 3 — Dimensions and scale

**Goal:** the actual point of the feature — numbers that survive the trip.

**Changes**

- `src/scale.ts` — `mm_per_px` from capture metadata; two-point calibration for
  device images; formatting; the `confidence` rules (`exact` / `approximate` /
  `none`). Dimensions render as a pixel ratio and are **not** quoted in mm when
  confidence is `none`.
- `src/dimensions.ts` — tap-tap placement, rubber-band draft, endpoint hit-test
  and snapping to existing endpoints (so a chain shares exact points rather than
  three slightly different taps at one corner), the value sheet
  (`measured_mm` shown, `target_mm` typed, optional note).
- `src/doc.ts` — the annotation document: normalized 0..1 image coordinates,
  `snapped_to: null` reserved, schema version field. Serialized into the upload's
  `doc` part.
- Python: `render` gains the scale derivation — `cam.height / rendered_height_px`
  read **after** `_apply_camera_plan` and the final size, `None` when
  `_ortho_camera` returns `None`, plus the `axis_aligned` flag from
  `_orbit_angles_from_view`. `send_to_device` defaults to a face-on view.
- `read_device_image` includes the annotation document verbatim in its text half,
  with the projection-plane caveat spelled out when the camera isn't axis-aligned.

**Verification that isn't a unit test:** capture a 100mm box and confirm its
on-image pixel width equals `100 / mm_per_px` within a pixel. This is the one
number in the design derived rather than measured — check it before trusting any
dimension the feature reports.

**Tests:** Vitest on `scale` (derivation, calibration, confidence downgrade,
formatting) and `doc` (round trip, normalized coords, measured-vs-target).

**Done when:** you can draw a dimension on a capture, type a target, send it, and
Claude quotes both numbers back and acts on the target.

---

## Phase 4 — Hardening and release wiring

**Goal:** safe to leave switched on, and shippable.

**Changes**

- Idle auto-stop (no request for N minutes), stop on workbench shutdown, clear
  server state on "New" so one chat's captures don't leak into the next.
- Error paths with a message rather than a stack trace: server not running,
  device offline mid-upload, disk full, no active document, non-3D active tab.
- `RELEASE.md`: `npm ci && npm run build`, commit the `device_ui/` diff, before
  bumping `package.xml`.
- `README.md` + `SECURITY.md`: what the LAN server exposes, in the plain terms of
  the design doc's security section — it binds a LAN interface, which the rest of
  the addon does not.
- `CLAUDE.md`: the module map gains `device_server.py`, `tools_device.py` and the
  `web/` → `device_ui/` build relationship; the tools table gains the two tools.

**Done when:** a fresh deploy on a clean FreeCAD profile works with no npm
present, and the feature is documented where a user would look.

---

## Phase 5 — The deferred list, in the order I'd add it

Each is independent; none blocks the others.

1. **Auto-inject on upload.** The panel appends *"the user sent an image:
   `<path>`"* to the next prompt so Claude looks without being asked. Clearly
   right for "here's what I want it to look like"; deferred only because it
   couples the panel to the server.
2. **Stroke eraser.** Cheap now that strokes are vectors — hit-test and delete.
3. **Pinch-zoom and pan.** Two-finger only, unambiguous because touch never
   draws. The view transform is already in place from phase 1. This is what makes
   the phone genuinely usable; the iPad mostly doesn't need it.
4. **The magnifier loupe** while dragging a dimension endpoint. At 1× a pen lands
   within ~3px, which on a 1600px capture of a 120mm part is ±0.2mm of error
   injected purely by the input method.
5. **Geometry snapping** (`snapped_to`). Unproject the tap through the recorded
   camera, Coin ray-pick against the document on the GUI thread, and a dimension
   becomes *"Vertex3 → Vertex7 of Pad001, 24.3mm true 3D"*. Kills the pixel error
   and the projection-plane caveat together, and hands `run_python` a real
   subelement name. The schema field exists from phase 3 so this is additive.
   Note this is the first thing that breaks the "server never calls into FreeCAD"
   invariant — it needs `gui_bridge._run_on_gui`, and it should be the tool that
   picks, not the HTTP handler.
6. **Blank graph-paper sheet.** Draw a concept from nothing with the grid as the
   scale (mm per square), feeding `freecad-lofi-sketch` with real dimensions
   instead of proportions.
7. **QR pairing.** Only if typing the URL turns out to be the thing that stops you
   using it. A pure-stdlib QR encoder is ~150 lines you'd then own.
