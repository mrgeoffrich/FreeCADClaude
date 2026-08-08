// SPDX-License-Identifier: LGPL-2.1-or-later
// Wiring: mount the UI, put a canvas under it, feed the pointer policy's
// samples into strokes, and import images from the device.
//
// Everything with logic worth testing lives in the modules this file pulls
// together; this is the part that can only be verified on a real tablet.
import { CanvasView, flattenToPng } from "./canvas";
import { attachDrawing, type PointerSample } from "./input";
import { addPoint, finishStroke, newStroke, PEN_CSS_WIDTH, type Stroke } from "./strokes";
import { resolveToken } from "./token";
import { mountUi, type SourceChoice } from "./ui";

const PEN_HINT = "Pen draws · touch is ignored while a stylus is in use";
const NO_IMAGE_HINT = "Pick an image to mark up";

const ui = mountUi();
const canvasView = new CanvasView(ui.canvas);

/** Where the current image came from. Phase 4 adds the `freecad_capture`
 * kind, which carries the camera metadata and (phase 5) the exact scale. */
interface ImageSource {
  readonly kind: "device_photo" | "device_file";
  readonly name: string;
}

let source: ImageSource | null = null;
let activeStroke: Stroke | null = null;

// ── pairing ────────────────────────────────────────────────────────────────
// The token isn't used until phase 4's fetches, but it has to be lifted off
// the URL on the load that carries it -- the query string is gone the moment
// the user reloads from a bookmark.
const token = resolveToken(window.location.search, window.sessionStorage);
if (token && window.location.search) {
  // Drop it from the visible URL so a screenshot or a shared link doesn't
  // carry it.
  history.replaceState(null, "", window.location.pathname);
}

// ── status ─────────────────────────────────────────────────────────────────
function refreshStatus(): void {
  ui.setStatus({
    title: source ? source.name : "No image",
    subtitle: source
      ? source.kind === "device_photo"
        ? "photo from device"
        : "file from device"
      : token
        ? "pick a source to start"
        : "not paired — reopen the link from FreeCAD",
    // Device images have no scale until phase 5's calibration.
    confidence: "none",
    connected: Boolean(token) && source !== null,
  });
  ui.setHint(source ? PEN_HINT : NO_IMAGE_HINT);
  ui.setSendEnabled(source !== null);
}

refreshStatus();
// Phase 4 flips this on once GET /api/latest reports a published capture.
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

async function loadFile(file: File, kind: ImageSource["kind"]): Promise<void> {
  try {
    const bitmap = await decodeImage(file);
    // A new base image is a new drawing -- ink is positioned in the old
    // image's space and would be meaningless over this one.
    canvasView.scene.strokes.length = 0;
    activeStroke = null;
    canvasView.setImage(bitmap);
    source = { kind, name: file.name || "photo" };
    refreshStatus();
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
  // "freecad" is phase 4; the choice is disabled until then.
};

// ── send ───────────────────────────────────────────────────────────────────
// Phase 4 replaces the download with a POST to /api/upload (the same blob,
// plus the caption and the annotation document). Until then, flattening to a
// file the user can see is what makes this phase verifiable on the device.
ui.onSend = () => {
  void (async () => {
    try {
      const blob = await flattenToPng(canvasView.scene);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `annotated-${Date.now()}.png`;
      a.click();
      // Revoking straight away races the download on both Safari and Chrome.
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
      ui.toast("Flattened PNG saved");
    } catch {
      ui.toast("Couldn't flatten the image", "bad");
    }
  })();
};

// ── layout ─────────────────────────────────────────────────────────────────
// The canvas is sized by CSS; the backing store follows it at
// devicePixelRatio, and the fit transform is recomputed from the new viewport.
const observer = new ResizeObserver(() => canvasView.resize());
observer.observe(ui.canvas);
canvasView.resize();
