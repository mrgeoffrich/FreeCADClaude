# Device annotation — implementation plan

Companion to `device-annotation-design.md`. Six phases, each one a commit that
leaves the addon working.

**Scope is unchanged from the design doc.** This plan regroups the same work
items; the only addition is QR pairing, which is now in scope. Nothing here
introduces a component the design doc doesn't already specify.

Ordering is by **dependency first, then size** — each phase depends only on
earlier ones, and where two are both unblocked the smaller goes first.

```
  1 Foundations ──┬── 2 Pairing (QR)          [leaf]
                  │
                  ├── 3 Canvas and ink ──┐
                  │                      ├── 5 Measurement ──┐
                  └── 4 Transport ───────┘                   ├── 6 Hardening
                              └───────────────────────────────┘
```

Phases 2 and 3 are independent of each other — 2 goes first only because it is
smaller and finishes the "get the page onto the device" story. Phase 4 needs the
flatten/downscale from 3; phase 5 needs both 3 and 4.

---

## Phase 1 — Foundations · size M

Everything depends on this: a build pipeline that produces committed output, and
a server that can hand a page to a device.

- `web/` Vite + TypeScript + Vitest project. One runtime dep: `perfect-freehand`.
  `vite.config.ts` with `base: "./"`, `outDir: "../freecad/freecadclaude/device_ui"`,
  no code splitting, assets inlined.
- `freecad/freecadclaude/device_server.py`: `start()` / `stop()`, ephemeral port
  on `0.0.0.0`, LAN address discovery, token, static serving from `device_ui/`
  with realpath containment. No API routes yet.
- Chat panel: a "Device" button that starts the server and shows the URL.
- `.gitignore`: add `web/node_modules/`, `web/dist/`; confirm
  `freecad/freecadclaude/device_ui/` is **not** ignored.
- `deploy.ps1` / `deploy.sh`: add `web` to the exclude lists.

**Tests:** `freecadcmd` — token rejection, path traversal rejection, a known
asset served.

**Done:** the page loads on both devices from a typed URL.

---

## Phase 2 — Pairing · size S–M

A leaf: depends only on phase 1's URL, and nothing depends on it.

- `freecad/freecadclaude/qr.py` — byte mode, EC level L, versions 3–6 (3–5 are
  one block, 6 is two equal ones, so the interleave is a `zip`), fixed mask 0
  with a hardcoded format-bit table, Reed–Solomon over GF(256). Returns a
  boolean matrix; imports no Qt.
- Chat panel popup: the QR rendered with `QPainter` into a `QPixmap` (8px
  modules, 4-module quiet zone), the URL as text beneath it, and a stop button.

**Tests:** `freecadcmd` — encode a known string, compare against a reference
matrix.

**Done:** scanning the code on either device opens the page authenticated.

---

## Phase 3 — Canvas and ink · size L

The frontend core. Depends on phase 1's scaffold only.

- `src/canvas.ts` — render loop at `devicePixelRatio`, base image + strokes, and
  the view transform (`{scale, tx, ty}`, identity for now). All coordinates are
  stored in image space and pass through it, so pinch-zoom stays a contained
  change later rather than a refactor of everything touching a coordinate.
- `src/input.ts` — the pointer policy as a pure `shouldDraw(state, event)`:
  pen-only once a stylus is seen, `touch-action: none`, `getCoalescedEvents()`.
- `src/strokes.ts` — perfect-freehand wrapper, `e.pressure` → width.
- `src/ui.ts` — the toolbar from the mockup: source, pen, undo, clear.
- Image import: file input for the library, a second with
  `capture="environment"` for the camera, `createImageBitmap(blob,
  {imageOrientation: "from-image"})` so phone photos don't arrive rotated.
- Flatten to PNG, downscale to a 1568px long edge.

**Tests:** Vitest on `input` (the pen-then-palm sequence) and on the
flatten/downscale geometry.

**Done:** you can import a photo on either device, mark it up, and get the
flattened PNG back out.

---

## Phase 4 — Transport · size L

The round trip. Depends on phase 1's server and phase 3's flatten.

- `device_server.py`: `GET /api/latest`, `GET /api/image/<id>`,
  `POST /api/upload` (12 MB cap, PNG/JPEG magic-byte check, generated names via
  `_artifact_path`), `GET /api/events` (SSE, woken by a `threading.Condition`),
  and `publish(path, meta)` for the tool to call.
- `freecad_tools/tools_device.py`: `send_to_device` (reuses
  `render._offscreen_shot` / `_capture_setup` / `_apply_camera_plan` — no new
  capture code) and `read_device_image`, returning `(text, png_path)` so
  `gui_bridge` base64s it into an inline image block. Both registered in
  `freecad_tools/__init__.py`. `send_to_device` returns immediately, per the
  two-halves rule.
- `src/api.ts` — token in `sessionStorage`, fetch wrappers, `EventSource`.
- Send button; incoming-capture banner (notify, don't clobber an in-progress
  drawing).
- Chat panel: "📱 image received" in the transcript on upload.

**Tests:** `freecadcmd` — upload validation (oversize, wrong magic bytes,
traversal in a filename), publish/fetch round trip.

**Done:** Claude sends you a view, you scribble on it, Claude describes the mark
with the right camera-angle and extents context.

---

## Phase 5 — Measurement · size L

Numbers that survive the trip. Depends on phases 3 and 4.

- `src/doc.ts` — the annotation document: normalized 0..1 image coordinates,
  `snapped_to: null` reserved, schema version field, serialized into the upload's
  `doc` part.
- `src/scale.ts` — `mm_per_px` from capture metadata, two-point calibration for
  device images, formatting, and the `confidence` rules (`exact` / `approximate`
  / `none`; dimensions render as a pixel ratio and are not quoted in mm at
  `none`).
- `src/dimensions.ts` — tap-tap placement, rubber-band draft, endpoint hit-test
  and snapping to existing endpoints, and the value sheet (`measured_mm` shown,
  `target_mm` typed, optional note).
- `render.py`: the scale derivation — `cam.height / rendered_height_px` read
  after `_apply_camera_plan` and the final size, `None` when `_ortho_camera`
  returns `None`, plus the `axis_aligned` flag from `_orbit_angles_from_view`.
  `send_to_device` defaults to a face-on view.
- `read_device_image` includes the annotation document verbatim, with the
  projection-plane caveat spelled out when the camera isn't axis-aligned.

**Tests:** Vitest on `scale` (derivation, calibration, confidence, formatting)
and `doc` (round trip, normalized coords, measured-vs-target).

**Done:** draw a dimension on a capture, type a target, send — Claude quotes both
numbers and acts on the target.

---

## Phase 6 — Hardening and docs · size M

Depends on everything.

- Idle auto-stop, stop on workbench shutdown, clear server state on "New" so one
  chat's captures don't leak into the next.
- Error paths with a message rather than a stack trace: server not running,
  device offline mid-upload, disk full, no active document, non-3D active tab.
- `RELEASE.md`: `npm ci && npm run build`, commit the `device_ui/` diff, before
  bumping `package.xml`.
- `README.md` + `SECURITY.md`: what the LAN server exposes.
- `CLAUDE.md`: module map gains `device_server.py`, `qr.py`, `tools_device.py`
  and the `web/` → `device_ui/` build relationship; tools table gains the two
  tools.

---

## Deferred

Unchanged from the design doc, minus QR (now phase 2). Each is independent; none
blocks the others.

1. **Auto-inject on upload** — the panel appends *"the user sent an image:
   `<path>`"* to the next prompt so Claude looks without being asked.
2. **Stroke eraser** — cheap now that strokes are vectors: hit-test and delete.
3. **Pinch-zoom and pan** — two-finger only, unambiguous because touch never
   draws; the view transform is already in place from phase 3.
4. **Magnifier loupe** while dragging a dimension endpoint.
5. **Geometry snapping** (`snapped_to`) — unproject the tap, Coin ray-pick, and a
   dimension becomes *"Vertex3 → Vertex7 of Pad001, 24.3mm true 3D"*. The schema
   field exists from phase 5, so it is additive. First thing that breaks the
   "server never calls into FreeCAD" invariant — it belongs in the tool, not the
   HTTP handler.
6. **Blank graph-paper sheet** — grid as the scale, feeding `freecad-lofi-sketch`
   with real dimensions.
