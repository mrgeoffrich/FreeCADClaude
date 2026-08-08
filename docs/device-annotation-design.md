# Device annotation — technical design

Send an image from FreeCAD to a tablet or phone on the same LAN, mark it up with
a pen, send it back with **structured dimensions** attached, and let Claude act on
it. Also the reverse direction: import a photo or an existing design from the
device as reference.

Two use cases drive it:

- **"Draw what I want done."** A screenshot of the model goes to the iPad, the
  user circles a boss and writes *make this 30mm*, it comes back.
- **"This is what I want it to look like."** A photo of a sketch, a product shot
  or an existing drawing comes off the device with no screenshot involved.

The second half of the first case is why this isn't just image transfer:
**a picture carries no scale.** A circled region tells Claude *where*; only a
dimension with a number tells it *how much*. So the payload is an image **and** a
small annotation document, and the two carry different things:

> The picture carries what JSON shouldn't — shape, gesture, emphasis, "this bit
> here". The JSON carries what the picture can't — numbers, identity, intent.

That is the same split `tools_annotate.py` already runs on (no pixel diffing;
Claude reads marks off the image; the code contributes the camera angle and world
extents the image can't hold). This extends it rather than replacing it.

## Scope

**In, for v1:**

- A local HTTP server in the FreeCAD process, LAN-bound, token-gated, off by
  default.
- A small web app: base image, pen strokes, dimension annotations, caption, send.
- Image sources: a capture pushed from FreeCAD, the device photo library, the
  device camera.
- Scale: exact for FreeCAD captures (derived from the ortho camera); user
  calibration for device images.
- Two tools: `send_to_device`, `read_device_image`.
- QR pairing: the chat panel shows a scannable code for the URL + token.

**Out, deliberately — these are cheap to add later and expensive to carry now:**

- Ray-picking a tapped point onto real geometry (`Pad001.Vertex3`). The schema
  reserves `snapped_to` for it; the Coin work is a later phase.
- Point labels, callouts, shape tools, text tool. Handwriting covers all of them
  and Claude reads it off the image.
- Pixel eraser. The eraser rubs out whole strokes, which is what vector ink
  supports without its own compositing layer.
- Layers, selection, transform, multi-image compositing.
- Blank graph-paper sheet (draw a concept from scratch). Wanted, but it needs its
  own scale story; it lands after dimensions work.

## The invariant that keeps this simple

**The HTTP server never calls into FreeCAD.** Not once, in v1.

Captures are *pushed*: `send_to_device` runs on the GUI thread as an ordinary
tool, writes a PNG plus its metadata into the session folder, and hands the
server a path. The device can only fetch files that already exist and POST new
ones. Nothing arriving over the network can cause a FreeCAD call, a recompute, or
a document mutation.

That buys three things at once: no `gui_bridge._run_on_gui` marshalling in the
server at all, no way for a LAN request to freeze the GUI thread, and a security
story that is one sentence long. `run_python` is arbitrary Python in the FreeCAD
process — an endpoint that could ever reach it would be a different animal from
one that reads and writes PNGs in a folder. Keep the distance.

## Architecture

```
chat panel (GUI thread)
  ├─ "Device" button ──▶ device_server.start()  ── daemon thread ──┐
  │                                                                │
  └─ AgentWorker ──▶ claude CLI ──▶ MCP ──▶ gui_bridge ──▶ tools_device
                                                              │     │
                              send_to_device  ────────────────┘     │
                                (capture PNG + meta ──▶ publish)    │
                              read_device_image ◀─────────────────  │
                                (newest upload + annotation JSON)   │
                                                                    ▼
      iPad / Samsung  ◀── HTTP (LAN, token) ──▶  ThreadingHTTPServer
        browser                                    static dist/
                                                   GET  /api/latest
                                                   POST /api/upload
                                                   GET  /api/events (SSE)
```

Files live under the existing per-session artifact folder
(`session.py`), in a new `mobile/` subdir alongside `captures/` and `exports/`,
pruned the same way by `_session_subdir`.

## Python side

### `freecad/freecadclaude/device_server.py`

Stdlib only, like `mcp_server.py`. `http.server.ThreadingHTTPServer` on a daemon
thread. Idempotent `start()` returning `(url, token)`, mirroring
`gui_bridge.start()`'s shape.

- **Bind** `0.0.0.0` on an ephemeral port. The displayed URL uses the primary
  LAN address, discovered with the standard UDP-connect trick
  (`socket.socket(AF_INET, SOCK_DGRAM).connect(("192.0.2.1", 1))` then
  `getsockname()[0]` — no packet is sent, and `192.0.2.x` is the reserved
  documentation range so it can never route anywhere).
- **Token**: `secrets.token_urlsafe(16)`, minted per start. Every request must
  carry it, as `?t=` on the page load or an `X-FC-Token` header on the API. The
  page stashes it in `sessionStorage` on first load.
- **Static files** come from one directory (`freecad/freecadclaude/device_ui/`)
  with `os.path.realpath` containment checks — no `SimpleHTTPRequestHandler`
  path handling, which resolves against the process cwd.
- **State** is a small in-process record: the currently published image + meta,
  and a list of uploads. A `threading.Condition` wakes SSE readers; no filesystem
  watching.
- **Lifecycle**: started explicitly from the chat panel, stopped on workbench
  shutdown. Not started at import, and not started by a tool call — the user
  turns it on.

### `freecad/freecadclaude/freecad_tools/tools_device.py`

`send_to_device(objects?, view?, azimuth?, elevation?, note?)` — reuses the
existing `capture_view` path (`render._offscreen_shot`, `_capture_setup`,
`_apply_camera_plan`) so framing, sizing and visibility isolation are not
reimplemented. It then computes the scale metadata (below), publishes to the
server, and **returns immediately** — same two-halves rule as `annotate_view`,
because a tool call must never block the GUI thread waiting for a human.

Defaults to a face-on axis-aligned view rather than the user's current orbit, for
the reason in "Scale" below: an oblique camera makes every measured distance
wrong.

If the server isn't running, the tool says so and tells the user which button to
press, rather than silently starting a LAN listener on their behalf.

`read_device_image(index?)` — returns the newest upload as
`(text, png_path)`, which `gui_bridge` already base64-encodes into an inline MCP
image block. The text half carries the annotation document (below) plus the
capture context replayed from publish time — camera angle, visible objects, world
extents — exactly as `read_annotation` replays `_last_annotation["context"]`.

### Chat panel

One button, next to "Files": toggles the server and opens a small popup with the
QR code, the URL underneath it as text, and a stop button. When an upload
arrives, the panel notes it in the transcript ("📱 image received") so the user
knows it landed even if Claude hasn't looked yet.

### `freecad/freecadclaude/qr.py`

Pairing is a scan, not a typed URL: `http://192.168.1.23:54321/?t=<22 chars>` is
about 54 characters of case-sensitive token, and typing that on a tablet once per
session is the kind of friction that decides whether a feature gets used.

No pip dependency is available, so this is a small stdlib encoder — and the scope
it needs is narrow enough to stay small:

- **Byte mode, error-correction level L, versions 3–6 only.** That covers 53 to
  134 characters, which brackets the URL with room for a longer host name. The
  cap at version 6 keeps out the version-information block (18 bits in two
  corners, versions 7+) and the multi-alignment-pattern table.
  *(This originally said versions 1–6 at level L are all single-block and so
  need no interleaving. That is wrong — version 6 is two blocks — and it cost a
  round of debugging in phase 2, since a single-block version 6 produces a
  structurally perfect symbol that decodes as nothing. What is true is that
  every block in versions 3–6 is the **same size**, so the interleave is a
  `zip` with no group-1/group-2 split.)*
- **Mask 0, fixed.** The spec allows any of the eight; choosing the best one is
  what the penalty-scoring pass is for, and skipping it costs nothing a reader
  will notice at this size. Format bits are then a hardcoded 8-entry table rather
  than a BCH computation.
- Reed–Solomon over GF(256) with the standard 0x11d primitive polynomial — log
  and antilog tables built once at import.

Rendering is `QPainter` into a `QPixmap` (8px modules, 4-module quiet zone) shown
in a `QLabel`. `qr.py` itself imports no Qt and returns a matrix of booleans, so
it is unit-testable headlessly against known-good codes.

## HTTP surface

| Route | Method | Purpose |
|---|---|---|
| `/` + `/assets/*` | GET | The built web app |
| `/api/latest` | GET | The currently published image's metadata (JSON) |
| `/api/image/<id>` | GET | The published image bytes |
| `/api/upload` | POST | `multipart/form-data`: `image` (PNG), `doc` (JSON) |
| `/api/events` | GET | SSE; one `published` event per `send_to_device` |

`/api/upload` caps the body at 12 MB, accepts PNG and JPEG only (magic-byte
check, not the declared content type), writes into
`<session>/mobile/`, and returns the stored filename. The client downscales to a
1568px long edge before POSTing — a 12MP phone photo is megabytes of wifi upload
and buys nothing, since that is roughly where the API's image blocks cap out
anyway.

## The annotation document

Sent with the upload, stored beside the PNG, and included verbatim in
`read_device_image`'s text half.

```json
{
  "image": "annot_8f21.png",
  "source": {
    "kind": "freecad_capture",
    "document": "Bracket",
    "camera": {"projection": "orthographic", "view": "front", "axis_aligned": true},
    "scale": {
      "mm_per_px": 0.0842,
      "confidence": "exact",
      "plane": "distances are true in the world X/Z plane; depth is not measurable"
    }
  },
  "annotations": [
    {"id": "d1", "type": "dimension",
     "a": [0.31, 0.62], "b": [0.58, 0.62],
     "measured_mm": 24.3, "target_mm": 30.0,
     "note": "widen the slot",
     "snapped_to": null}
  ],
  "caption": "slot too narrow"
}
```

Decisions worth not undoing:

- **Coordinates are normalized 0..1 in image space**, never screen pixels. The
  document then survives any resize, any zoom level, and any device.
- **Freehand strokes are not serialized.** Claude sees them in the image; a point
  list would be tokens spent on something already visible. The JSON is only for
  things carrying a number or a name.
- **`measured_mm` and `target_mm` are different facts.** *"This is 24.3mm"* is
  information; *"make this 30mm"* is an instruction. Keeping both lets Claude see
  the delta and lets it distinguish a dimension the user was merely reading off
  from one they were asking for. `target_mm` is null until the user types one.
- **`confidence` is part of the payload, not an assumption.** `exact` (derived
  from the ortho camera), `approximate` (user-calibrated photo), or `none` (no
  scale — dimensions are pixel ratios only and must not be quoted in mm).
- `snapped_to` is reserved for geometry picking and is always `null` in v1.

## Scale

**FreeCAD captures — exact.** For an orthographic camera, `height` is the world
height of the viewing volume, so `mm_per_px = cam.height / rendered_height_px`.
`render._ortho_camera` already exists and already returns `None` for a
non-orthographic camera, which is exactly the "no scale available" signal. The
number must be read **after** `_apply_camera_plan` and the final render size are
applied, for the same reason `_fit_render_size` runs there — how wide the box
looks depends on where the camera ended up.

**The projection-plane caveat, which must reach Claude.** Unprojecting a screen
point through an ortho camera gives a ray, not a point. A distance between two
screen points is a distance *in the projection plane*: on a `front` view, world X
and Z are true and depth is unmeasurable; on an oblique view everything is
foreshortened and the number is simply wrong. So the payload records the view and
an `axis_aligned` flag, `send_to_device` defaults to a face-on view, and when the
camera is oblique the metadata downgrades `confidence` rather than omitting the
number — Claude needs to tell "no measurement" from "a measurement you shouldn't
machine to".

**Device images — user-calibrated.** Two taps plus a typed real length gives
mm/px for that image. Valid only for a roughly planar, roughly face-on subject;
a photo shot at an angle is right along the calibration line and wrong everywhere
else. It is therefore always `"confidence": "approximate"`, and Claude should
hedge accordingly rather than modelling to 0.1mm off a snapshot of a napkin.

## Input policy

The single most important detail for a pen device, and about five lines:

```ts
if (e.pointerType === "pen") state.penSeen = true;
if (state.penSeen && e.pointerType === "touch") return;  // palm rejection, entire
```

Once a stylus has been seen, touch never draws. Palm rest works, and touch is
freed up for navigation later without ambiguity. Both the S-Pen and the Apple
Pencil report through the same PointerEvent path.

Also required, and each of them is a bug if missed: `touch-action: none` on the
canvas (or the browser steals the gesture to scroll), `getCoalescedEvents()` (a
120Hz iPad delivers several samples per frame and a naive handler drops most of
them), rendering at `devicePixelRatio`, and `e.pressure` mapped to stroke width
through perfect-freehand.

**Every annotation coordinate is stored in image space** and passed through a
view transform (`{scale, tx, ty}`) at render and hit-test time. Pinch-zoom
landed after v1 as `clampView` plus a `setView` call, which is what the
transform existing from day one bought: a contained change instead of a
refactor of everything that touches a coordinate.

**Navigation is two fingers, never one.** A single contact is a drawing stroke
on a penless phone and a resting palm on a tablet, so panning off it would move
the image while the user is marking it up.

## Security

This binds a LAN interface, which is a real change from the existing
localhost-only `gui_bridge`. The threat model is *someone else on your wifi*, and
the mitigations are:

- **Separate socket, separate token, separate handler** from `gui_bridge`. Never
  a new route on the existing bridge.
- **No FreeCAD reachability at all** (the invariant above). The worst a
  token-holder can do is read published captures and write image files into the
  session folder.
- **Off by default**, started explicitly, stopped on shutdown. An idle timeout is
  phase 6 hardening.
- Uploads: size-capped, magic-byte checked, written with generated names via the
  existing `_artifact_path` (never a client-supplied filename), pruned by
  `_session_subdir`.
- Static serving from one realpath-contained directory.
- No CORS headers at all — the page is same-origin with the API.

Plain HTTP means the token crosses the LAN in clear. For a personal-use addon on
a home network that is an accepted trade, and it is worth stating plainly rather
than implying TLS. It also caps what's possible on the client: `getUserMedia`
needs a secure context, so there is no live in-page viewfinder — but
`<input type="file" accept="image/*" capture="environment">` opens the camera app
over plain HTTP and always has, which covers the actual requirement.

## Frontend build and distribution

Vite + TypeScript + Vitest, one runtime dependency (`perfect-freehand`, ~4KB).
No React: five toolbar buttons and a canvas do not need a component framework,
and the annotation document is a plain typed object, not application state that
benefits from one.

```
web/                        # Vite project — dev-time only, excluded from deploy
  package.json  vite.config.ts  tsconfig.json  index.html
  src/
    main.ts        wiring
    api.ts         fetch + token + SSE
    canvas.ts      render loop, view transform
    input.ts       pointer policy               ← unit tested
    strokes.ts     perfect-freehand wrapper
    dimensions.ts  dimension objects, hit-test  ← unit tested
    scale.ts       mm/px, calibration, formatting ← unit tested
    doc.ts         annotation document          ← unit tested
    ui.ts          toolbar, caption, sheets
  test/
freecad/freecadclaude/device_ui/    # BUILT OUTPUT — committed to git
```

**The build output is committed, and that is not laziness.** Users install from
the `main` **branch** via the Addon Manager (see `RELEASE.md`) — they get a plain
file copy with no Node, no npm and no build step. If `device_ui/` isn't in the
tree, the feature doesn't exist for them. Consequences to wire up:

- `deploy.ps1` / `deploy.sh` exclude lists gain `web` (source, and its
  `node_modules`), while `freecad/` — and therefore `device_ui/` — copies as it
  already does.
- `.gitignore` gains `web/node_modules/` and `web/dist/`, and must **not** ignore
  `freecad/freecadclaude/device_ui/`.
- `vite.config.ts` sets `base: "./"`, `outDir` to the committed folder, and
  disables code splitting + inlines assets, so the served tree is a handful of
  files rather than a hashed graph.
- `RELEASE.md` gains one step: `npm ci && npm run build`, commit the diff, before
  bumping `package.xml`.

## Testing

Vitest covers the pure logic, which is deliberately most of the interesting code:

- `scale`: mm/px derivation, calibration from two points and a length, rounding
  and unit formatting, the `confidence` downgrade rules.
- `doc`: serialize/deserialize round trip, normalized-coordinate conversion,
  `measured_mm` vs `target_mm` semantics, schema version handling.
- `input`: the pointer policy as a pure `shouldDraw(state, event)` — the
  pen-then-palm sequence is a unit test, not something to discover on a tablet.
- `dimensions`: endpoint hit-testing and snapping under a non-identity view
  transform.

Canvas rendering and gesture feel are **not** unit tested; they are verified on
the two real devices, which is the only place they can be.

Python side: `freecadcmd` scripts for the server's request handling (token
rejection, path traversal, upload validation, magic-byte check) and for `qr.py`
(encode a known string, compare against a reference matrix) — none of it needs a
GUI, since neither module imports Qt.

## Open questions

1. **Auto-inject on upload?** When an image lands, the panel could append a note
   to the next prompt (*"the user sent an image: `<path>`"*) so Claude looks
   without being asked. Clearly right for the "here's what I want it to look
   like" case; it couples the panel to the server, so it's proposed as phase 5
   rather than assumed.
2. **One device or several?** The design allows several — the token is not
   per-device and SSE fans out. No reason to restrict it, but it is untested.
3. **What happens to an in-progress drawing when a new capture is published?**
   Proposed: notify, don't clobber. The user taps to load it.
