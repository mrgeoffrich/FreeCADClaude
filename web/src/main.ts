// SPDX-License-Identifier: LGPL-2.1-or-later
// Wiring: mount the UI, put a canvas under it, feed the pointer policy's
// samples into strokes, and import images from the device.
//
// Everything with logic worth testing lives in the modules this file pulls
// together; this is the part that can only be verified on a real tablet.
import { deviceApi, describeCapture, type AnnotationDoc, type Published } from "./api";
import { CanvasView, flattenToPng } from "./canvas";
import { attachDrawing, type PointerSample } from "./input";
import { addPoint, finishStroke, newStroke, PEN_CSS_WIDTH, type Stroke } from "./strokes";
import { resolveToken } from "./token";
import { mountUi, type SourceChoice } from "./ui";

const PEN_HINT = "Pen draws · touch is ignored while a stylus is in use";
const NO_IMAGE_HINT = "Pick an image to mark up";

const ui = mountUi();
const canvasView = new CanvasView(ui.canvas);

/** Where the current image came from. A `freecad_capture` carries the
 * published record, which is what tells `read_device_image` on the way back
 * which camera angle and extents the marks were drawn against -- and, in phase
 * 5, the exact mm/px. */
interface ImageSource {
  readonly kind: "device_photo" | "device_file" | "freecad_capture";
  readonly name: string;
  readonly published?: Published;
}

let source: ImageSource | null = null;
let activeStroke: Stroke | null = null;

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
    // Nothing carries a scale until phase 5 derives it from the capture's
    // camera (or the user calibrates a photo).
    confidence: "none",
    connected: Boolean(token) && source !== null,
  });
  ui.setHint(source ? PEN_HINT : NO_IMAGE_HINT);
  ui.setSendEnabled(source !== null);
}

refreshStatus();
ui.setFreecadSource(false, "Arrives when FreeCAD sends you a view");

// ── drawing ────────────────────────────────────────────────────────────────
function pointOf(sample: PointerSample) {
  const p = canvasView.imageFromClient(sample.clientX, sample.clientY);
  return { x: p.x, y: p.y, pressure: sample.pressure > 0 ? sample.pressure : 0.5 };
}

attachDrawing(ui.canvas, {
  onStart(sample) {
    // Nothing to draw on, and no meaningful image-space coordinates either --
    // the view transform is identity until an image sets the fit.
    if (!canvasView.scene.image) return;
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
    if (!activeStroke) return;
    addPoint(activeStroke, pointOf(sample));
    canvasView.requestRender();
  },

  onEnd(commit) {
    if (!activeStroke) return;
    if (commit) {
      finishStroke(activeStroke);
    } else {
      const at = canvasView.scene.strokes.indexOf(activeStroke);
      if (at >= 0) canvasView.scene.strokes.splice(at, 1);
    }
    activeStroke = null;
    canvasView.requestRender();
  },
});

ui.onUndo = () => {
  canvasView.scene.strokes.pop();
  canvasView.requestRender();
};

ui.onClear = () => {
  canvasView.scene.strokes.length = 0;
  canvasView.requestRender();
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
  activeStroke = null;
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
ui.onSend = () => {
  void (async () => {
    ui.setSendEnabled(false);
    try {
      const png = await flattenToPng(canvasView.scene);
      const doc: AnnotationDoc = {
        caption: ui.caption.value.trim(),
        // Phase 5 fills the rest of this document in (dimensions, and the
        // scale that makes them mean millimetres). `source` is here now
        // because it is what read_device_image resolves the capture context
        // through -- without it a marked-up capture arrives with no camera
        // angle attached.
        source: source?.published
          ? { kind: "freecad_capture", id: source.published.id }
          : source
            ? { kind: source.kind }
            : null,
      };
      await api.upload(png, doc);
      // Clear what was sent: the marks are gone to Claude, and leaving them
      // under the next capture is how the same note gets sent twice.
      canvasView.scene.strokes.length = 0;
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
