// SPDX-License-Identifier: LGPL-2.1-or-later
// Wiring: mount the UI, put a canvas under it, feed the pointer policy's
// samples into strokes, and import images from the device.
//
// Everything with logic worth testing lives in the modules this file pulls
// together; this is the part that can only be verified on a real tablet.
import { deviceApi, describeCapture, type Published } from "./api";
import { CanvasView, flattenToPng, type Point } from "./canvas";
import {
  dimensionId,
  drawDimensions,
  endpointAt,
  newDimension,
  setEndpoint,
  snap,
  type Dimension,
  type DimensionDraft,
  type EndpointRef,
} from "./dimensions";
import { buildDoc, type DocSourceInput } from "./doc";
import { attachDrawing, type PointerSample } from "./input";
import {
  calibrationScale,
  captureScale,
  measureLabel,
  noScale,
  scaleStatusText,
  type Scale,
} from "./scale";
import { addPoint, finishStroke, newStroke, PEN_CSS_WIDTH, type Stroke } from "./strokes";
import { resolveToken } from "./token";
import { mountUi, type SourceChoice, type Tool } from "./ui";

const PEN_HINT = "Pen draws · touch is ignored while a stylus is in use";
const NO_IMAGE_HINT = "Pick an image to mark up";
const DIM_HINT = "Tap the two ends of what you want measured";
const DIM_SECOND_HINT = "Tap the second point";
const CALIBRATE_HINT = "Tap two points you know the real distance between";

/** How far a pointer has to travel for the gesture to count as a drag rather
 * than a tap, in image pixels. A dimension can be placed either way -- drag
 * across it, or tap each end -- and this is what tells them apart. */
const DRAG_MIN = 6;

const ui = mountUi();
const canvasView = new CanvasView(ui.canvas);

/** Where the current image came from. A `freecad_capture` carries the
 * published record, which is what tells `read_device_image` on the way back
 * which camera angle and extents the marks were drawn against -- and what the
 * exact mm/px is derived from. */
interface ImageSource {
  readonly kind: "device_photo" | "device_file" | "freecad_capture";
  readonly name: string;
  readonly published?: Published;
}

let source: ImageSource | null = null;
let activeStroke: Stroke | null = null;

// ── measurement state ──────────────────────────────────────────────────────
let tool: Tool = "pen";
/** What a pixel is worth on the loaded image. Replaced wholesale by `adopt`
 * (a new image is a new scale) and by a successful calibration. */
let scale: Scale = noScale({ width: 0, height: 0 });
const dimensions: Dimension[] = [];
/** Undo has to pop whichever mark was made last, and ink and dimensions live
 * in separate arrays -- so the order they were made in is kept here rather
 * than inferred. */
const undoHistory: Array<"stroke" | "dimension"> = [];
let dimensionSeq = 0;

/** The dimension being placed: first point down, second following the pen. */
let draft: DimensionDraft | null = null;
/** The endpoint a gesture grabbed, and whether it has actually moved -- a tap
 * on a handle that never moves opens its value sheet instead of dragging it. */
let dragging: EndpointRef | null = null;
let dragMoved = false;

/** The two taps of an in-progress calibration, waiting for a typed length. */
let calibration: DimensionDraft | null = null;
/** Set by the calibration sheet's Skip. The offer is made once per image: a
 * user who has said "no millimetres, thanks" should not be asked again every
 * time they place a dimension. */
let calibrationDeclined = false;

/** Which dimension the value sheet is currently editing. */
let editing: Dimension | null = null;

// ── pairing ────────────────────────────────────────────────────────────────
// The token has to be lifted off the URL on the load that carries it -- the
// query string is gone the moment the user reloads from a bookmark.
const token = resolveToken(window.location.search, window.sessionStorage);
if (token && window.location.search) {
  // Drop it from the visible URL so a screenshot or a shared link doesn't
  // carry it.
  history.replaceState(null, "", window.location.pathname);
}
const api = deviceApi(token);

/** The most recent capture FreeCAD has published, whether or not it's loaded.
 * Kept so the source sheet and the banner both act on the same thing. */
let available: Published | null = null;

// ── status ─────────────────────────────────────────────────────────────────
const SOURCE_SUBTITLE: Record<ImageSource["kind"], string> = {
  device_photo: "photo from device",
  device_file: "file from device",
  freecad_capture: "from FreeCAD",
};

/** The line under the canvas: what this tool is about to do, in the state it
 * is actually in. Kept in one place because the dimension tool has three
 * (calibrating / first point / second point) and they only differ by a word. */
function currentHint(): string {
  if (!source) return NO_IMAGE_HINT;
  if (tool !== "dimension") return PEN_HINT;
  if (calibrating()) return CALIBRATE_HINT;
  return draft ? DIM_SECOND_HINT : DIM_HINT;
}

function refreshStatus(): void {
  ui.setStatus({
    title: source ? source.name : "No image",
    subtitle: source
      ? source.kind === "freecad_capture" && source.published
        ? describeCapture(source.published)
        : SOURCE_SUBTITLE[source.kind]
      : token
        ? "pick a source to start"
        : "not paired — reopen the link from FreeCAD",
    confidence: scale.confidence,
    scaleText: scaleStatusText(scale),
    connected: Boolean(token) && source !== null,
  });
  ui.setHint(currentHint());
  ui.setSendEnabled(source !== null);
}

refreshStatus();
ui.setFreecadSource(false, "Arrives when FreeCAD sends you a view");

// ── drawing ────────────────────────────────────────────────────────────────
function pointOf(sample: PointerSample) {
  const p = canvasView.imageFromClient(sample.clientX, sample.clientY);
  return { x: p.x, y: p.y, pressure: sample.pressure > 0 ? sample.pressure : 0.5 };
}

function imagePoint(sample: PointerSample): Point {
  return canvasView.imageFromClient(sample.clientX, sample.clientY);
}

/** The dimension layer is drawn by canvas.ts through this hook -- on screen
 * every frame, and again into the flattened PNG, from the same code. A
 * dimension that only existed on the tablet would leave the JSON pointing at a
 * mark Claude can't see. */
canvasView.scene.overlay = (ctx, view, chrome) =>
  drawDimensions(ctx, view, { items: dimensions, draft, calibration, scale }, chrome);

attachDrawing(ui.canvas, {
  onStart(sample) {
    // Nothing to draw on, and no meaningful image-space coordinates either --
    // the view transform is identity until an image sets the fit.
    if (!canvasView.scene.image) return;
    if (tool === "dimension") {
      startDimensionGesture(imagePoint(sample));
      return;
    }
    // Ink is sized in CSS pixels and converted into image space, so a stroke
    // looks the same thickness under the pen whatever the image's resolution.
    const size = PEN_CSS_WIDTH / (canvasView.view.scale || 1);
    // Only a stylus reports real force; everything else reports a constant,
    // which renders as a dead uniform line unless perfect-freehand infers
    // pressure from velocity instead.
    activeStroke = newStroke(size, sample.pointerType !== "pen");
    canvasView.scene.strokes.push(activeStroke);
    addPoint(activeStroke, pointOf(sample));
    canvasView.requestRender();
  },

  onMove(sample) {
    if (tool === "dimension") {
      moveDimensionGesture(imagePoint(sample));
      return;
    }
    if (!activeStroke) return;
    addPoint(activeStroke, pointOf(sample));
    canvasView.requestRender();
  },

  onEnd(commit) {
    if (tool === "dimension") {
      endDimensionGesture(commit);
      return;
    }
    if (!activeStroke) return;
    if (commit) {
      finishStroke(activeStroke);
      undoHistory.push("stroke");
    } else {
      const at = canvasView.scene.strokes.indexOf(activeStroke);
      if (at >= 0) canvasView.scene.strokes.splice(at, 1);
    }
    activeStroke = null;
    canvasView.requestRender();
  },
});

// The rubber band has to follow a HOVERING pen between the two taps, and
// `attachDrawing` only reports moves for the pointer that is actually down.
// Five lines here rather than a second gesture concept in input.ts, whose one
// job is the pen/palm policy.
ui.canvas.addEventListener("pointermove", (e) => {
  if (tool !== "dimension" || draft === null || dragging !== null) return;
  moveDimensionGesture(canvasView.imageFromClient(e.clientX, e.clientY));
});

ui.onUndo = () => {
  const last = undoHistory.pop();
  if (last === "dimension") dimensions.pop();
  else if (last === "stroke") canvasView.scene.strokes.pop();
  canvasView.requestRender();
};

ui.onClear = () => {
  canvasView.scene.strokes.length = 0;
  dimensions.length = 0;
  undoHistory.length = 0;
  draft = null;
  calibration = null;
  canvasView.requestRender();
  refreshStatus();
};

ui.onTool = (next) => {
  tool = next;
  // A half-placed dimension belongs to the tool that started it.
  draft = null;
  dragging = null;
  canvasView.requestRender();
  refreshStatus();
};

// ── dimensions ─────────────────────────────────────────────────────────────
/** True while the dimension tool's next pair of taps is a calibration rather
 * than a measurement: an image with no scale, whose user hasn't declined. */
function calibrating(): boolean {
  return scale.confidence === "none" && !calibrationDeclined;
}

function startDimensionGesture(p: Point): void {
  const view = canvasView.view;
  // An existing handle wins over placing a new point -- but only when nothing
  // is half-placed, or the second tap of a pair could never land on the first.
  if (draft === null) {
    const hit = endpointAt(dimensions, view, p);
    if (hit) {
      dragging = hit;
      dragMoved = false;
      return;
    }
  }
  const snapped = snap(dimensions, view, p);
  if (draft === null) {
    draft = { a: snapped, b: snapped };
  } else {
    completePair(draft.a, snapped);
  }
  canvasView.requestRender();
  refreshStatus();
}

function moveDimensionGesture(p: Point): void {
  const view = canvasView.view;
  if (dragging) {
    setEndpoint(dragging, snap(dimensions, view, p, undefined, dragging));
    dragMoved = true;
  } else if (draft) {
    draft = { a: draft.a, b: snap(dimensions, view, p) };
  } else {
    return;
  }
  canvasView.requestRender();
}

function endDimensionGesture(commit: boolean): void {
  if (dragging) {
    const ref = dragging;
    dragging = null;
    // A handle tapped but not moved is "let me edit this one", which is the
    // only way back into a dimension's target once the sheet has closed.
    if (!dragMoved) openValueSheet(ref.dimension);
    canvasView.requestRender();
    return;
  }
  if (!commit) {
    draft = null;
  } else if (draft && Math.hypot(draft.b.x - draft.a.x, draft.b.y - draft.a.y) > DRAG_MIN) {
    // Dragged across the feature in one gesture; tapping instead leaves the
    // draft standing for a second tap.
    completePair(draft.a, draft.b);
  }
  canvasView.requestRender();
  refreshStatus();
}

/** Two points are down. They are either the calibration pair or a dimension --
 * the placement gesture is identical, only what it means differs. */
function completePair(a: Point, b: Point): void {
  draft = null;
  if (calibrating()) {
    calibration = { a, b };
    ui.openCalibrate();
    return;
  }
  addDimension(a, b);
}

function addDimension(a: Point, b: Point): void {
  dimensionSeq += 1;
  const dimension = newDimension(dimensionId(dimensionSeq), a, b);
  dimensions.push(dimension);
  undoHistory.push("dimension");
  openValueSheet(dimension);
}

function openValueSheet(dimension: Dimension): void {
  editing = dimension;
  ui.openValue({
    measured: measureLabel(scale, dimension.a, dimension.b),
    targetMm: dimension.targetMm,
    note: dimension.note,
  });
}

ui.onValueDone = (targetMm, note) => {
  if (editing) {
    editing.targetMm = targetMm;
    editing.note = note;
  }
  editing = null;
  canvasView.requestRender();
};

ui.onValueDelete = () => {
  const at = editing ? dimensions.indexOf(editing) : -1;
  if (at >= 0) {
    dimensions.splice(at, 1);
    // Drop one dimension entry from the undo history too, or Undo would later
    // try to pop a dimension that is already gone and eat an unrelated stroke.
    const entry = undoHistory.lastIndexOf("dimension");
    if (entry >= 0) undoHistory.splice(entry, 1);
  }
  editing = null;
  canvasView.requestRender();
};

ui.onCalibrate = (mm) => {
  const pair = calibration;
  calibration = null;
  const next = pair && calibrationScale(pair.a, pair.b, mm, scale.pixels);
  if (next) {
    scale = next;
    ui.toast(`Scale set — ${scaleStatusText(scale)}`);
  } else {
    // Two taps in the same spot, or a length that isn't a number: the scale is
    // left alone rather than set to something absurd.
    ui.toast("Those two points are too close together to set a scale", "bad");
  }
  canvasView.requestRender();
  refreshStatus();
};

ui.onCalibrateSkip = () => {
  calibrationDeclined = true;
  const pair = calibration;
  calibration = null;
  // The two taps become the dimension they would have been. They marked a real
  // span; without a scale it is quoted in pixels, which is what "no scale"
  // means everywhere else in the app.
  if (pair) addDimension(pair.a, pair.b);
  canvasView.requestRender();
  refreshStatus();
};

// ── image import ───────────────────────────────────────────────────────────
const libraryInput = document.getElementById("file-library") as HTMLInputElement;
const cameraInput = document.getElementById("file-camera") as HTMLInputElement;

/** Decode a picked file.
 *
 * `imageOrientation: "from-image"` is what keeps a phone photo from arriving
 * rotated 90°: the sensor writes landscape pixels plus an EXIF orientation
 * tag, and a bitmap decoded without it is sideways. Older Safari has
 * createImageBitmap but not the option, so the fallback goes via an <img>,
 * which applies EXIF orientation itself. */
async function decodeImage(file: File): Promise<ImageBitmap> {
  try {
    return await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    const url = URL.createObjectURL(file);
    try {
      const img = new Image();
      img.src = url;
      await img.decode();
      return await createImageBitmap(img);
    } finally {
      URL.revokeObjectURL(url);
    }
  }
}

/** Put a decoded bitmap on the canvas as the new base image.
 *
 * A new base image is a new drawing: ink is positioned in the OLD image's
 * space, so keeping it would leave marks pointing at nothing. Everything that
 * replaces the image goes through here so that rule is stated once. */
function adopt(bitmap: ImageBitmap, next: ImageSource): void {
  canvasView.scene.strokes.length = 0;
  dimensions.length = 0;
  undoHistory.length = 0;
  activeStroke = null;
  draft = null;
  dragging = null;
  calibration = null;
  calibrationDeclined = false;
  editing = null;
  dimensionSeq = 0;
  // A new image is a new scale, and it comes from the image itself: a capture
  // publishes the mm/px its ortho camera implies, and anything else starts
  // with none until the user calibrates. Carrying the old one over would quote
  // a photo in the previous capture's millimetres.
  const pixels = { width: bitmap.width, height: bitmap.height };
  scale = next.published ? captureScale(next.published.meta, pixels) : noScale(pixels);
  canvasView.setImage(bitmap);
  source = next;
  refreshStatus();
}

async function loadFile(file: File, kind: ImageSource["kind"]): Promise<void> {
  try {
    adopt(await decodeImage(file), { kind, name: file.name || "photo" });
  } catch {
    ui.toast("Couldn't read that image", "bad");
  }
}

function pickFrom(input: HTMLInputElement, kind: ImageSource["kind"]): void {
  input.onchange = () => {
    const file = input.files?.[0];
    // Reset first: picking the same file twice must still fire a change.
    input.value = "";
    if (file) void loadFile(file, kind);
  };
  input.click();
}

ui.onSource = (choice: SourceChoice) => {
  if (choice === "camera") pickFrom(cameraInput, "device_photo");
  else if (choice === "library") pickFrom(libraryInput, "device_file");
  else if (choice === "freecad" && available) void loadPublished(available);
};

// ── captures from FreeCAD ──────────────────────────────────────────────────
/** Note that a capture exists and offer it, without taking the canvas.
 *
 * Open question 3 of the design doc, answered the conservative way: a capture
 * arriving must NOT clobber a drawing in progress. The ink is minutes of the
 * user's pen work and the capture is one tap away either way, so the only case
 * that loads on its own is the one with nothing to lose -- an empty canvas,
 * which is exactly the "FreeCAD sent me a view" flow the feature is for. */
function offer(published: Published, live: boolean): void {
  available = published;
  ui.setFreecadSource(true, describeCapture(published));
  if (!canvasView.scene.image) {
    void loadPublished(published);
  } else if (live) {
    ui.showBanner(`New capture from FreeCAD — ${describeCapture(published)}`);
  }
}

async function loadPublished(published: Published): Promise<void> {
  try {
    const blob = await api.image(published);
    const bitmap = await createImageBitmap(blob);
    adopt(bitmap, {
      kind: "freecad_capture",
      name: published.meta.document || "FreeCAD",
      published,
    });
    ui.hideBanner();
    // Claude's "what to mark" line, if it sent one. In the hint rather than a
    // toast: it is an instruction the user acts on while drawing, and a toast
    // is gone in under three seconds. `adopt` has just reset the hint to the
    // pen policy, so this replaces it.
    if (published.meta.note) ui.setHint(published.meta.note);
  } catch {
    ui.toast("Couldn't load that capture", "bad");
  }
}

ui.onLoadIncoming = () => {
  if (available) void loadPublished(available);
};

// What's already there on load (the capture may predate this page), then the
// stream for everything after it. The server pushes over SSE rather than being
// polled -- see device_server's `_stream_events`.
void (async () => {
  try {
    const published = await api.latest();
    if (published) offer(published, false);
  } catch {
    // Unpaired, or FreeCAD stopped the server. The status bar already says so.
  }
})();
api.events((published) => offer(published, true));

// ── send ───────────────────────────────────────────────────────────────────
/** Where the image came from, as the document records it.
 *
 * `id` is not decoration: it is what `read_device_image` resolves the capture
 * context through -- without it a marked-up capture arrives with no camera
 * angle attached. The camera fields come with it so the document says, on its
 * own, which projection its millimetres live in. */
function docSource(): DocSourceInput | null {
  if (!source) return null;
  const published = source.published;
  if (!published) return { kind: source.kind };
  const meta = published.meta;
  return {
    kind: "freecad_capture",
    id: published.id,
    document: meta.document,
    view: meta.view ?? null,
    projection: meta.camera?.projection ?? null,
    axisAligned: meta.camera?.axis_aligned ?? null,
  };
}

ui.onSend = () => {
  void (async () => {
    ui.setSendEnabled(false);
    try {
      // A half-placed dimension is a rubber band, not a mark: dropping it here
      // keeps a dashed line to nowhere out of the picture Claude receives.
      draft = null;
      calibration = null;
      const png = await flattenToPng(canvasView.scene);
      const doc = buildDoc({
        caption: ui.caption.value.trim(),
        source: docSource(),
        scale,
        dimensions,
      });
      await api.upload(png, doc);
      // Clear what was sent: the marks are gone to Claude, and leaving them
      // under the next capture is how the same note gets sent twice.
      canvasView.scene.strokes.length = 0;
      dimensions.length = 0;
      undoHistory.length = 0;
      draft = null;
      canvasView.requestRender();
      ui.caption.value = "";
      ui.toast("Sent to Claude");
    } catch (err) {
      ui.toast(err instanceof Error ? `Couldn't send — ${err.message}` : "Couldn't send", "bad");
    } finally {
      ui.setSendEnabled(source !== null);
    }
  })();
};

// ── layout ─────────────────────────────────────────────────────────────────
// The canvas is sized by CSS; the backing store follows it at
// devicePixelRatio, and the fit transform is recomputed from the new viewport.
const observer = new ResizeObserver(() => canvasView.resize());
observer.observe(ui.canvas);
canvasView.resize();
