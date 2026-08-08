// SPDX-License-Identifier: LGPL-2.1-or-later
// The drawing surface: the view transform, the render loop, and flattening
// what's on screen down to a PNG.
//
// THE VIEW TRANSFORM IS THE POINT OF THIS FILE. Every coordinate the app
// stores -- ink points and dimension endpoints -- lives in IMAGE space and
// passes through `ViewTransform` at render and hit-test time. Nothing anywhere
// stores a screen pixel, which is why the pinch gesture is `setView` plus the
// pure helpers below rather than a change to everything that touches a
// coordinate.
import { drawStroke, type Stroke } from "./strokes";

export interface Point {
  readonly x: number;
  readonly y: number;
}

export interface Size {
  readonly width: number;
  readonly height: number;
}

/** screen = image * scale + (tx, ty), in CSS pixels relative to the canvas.
 * devicePixelRatio is applied on top of this at draw time and is NOT part of
 * it -- the transform is about where the image sits, not how dense the
 * display is. */
export interface ViewTransform {
  readonly scale: number;
  readonly tx: number;
  readonly ty: number;
}

export function identityView(): ViewTransform {
  return { scale: 1, tx: 0, ty: 0 };
}

/** The scale of a contain-fit, or 0 if either box is degenerate. */
export function fitScale(image: Size, viewport: Size): number {
  if (image.width <= 0 || image.height <= 0 || viewport.width <= 0 || viewport.height <= 0) {
    return 0;
  }
  return Math.min(viewport.width / image.width, viewport.height / image.height);
}

/** Contain-fit `image` inside `viewport`, centred. */
export function fitView(image: Size, viewport: Size): ViewTransform {
  const scale = fitScale(image, viewport);
  if (scale <= 0) return identityView();
  return {
    scale,
    tx: (viewport.width - image.width * scale) / 2,
    ty: (viewport.height - image.height * scale) / 2,
  };
}

export function toScreen(view: ViewTransform, p: Point): Point {
  return { x: p.x * view.scale + view.tx, y: p.y * view.scale + view.ty };
}

export function toImage(view: ViewTransform, p: Point): Point {
  return { x: (p.x - view.tx) / view.scale, y: (p.y - view.ty) / view.scale };
}

/** How far past the contain-fit a pinch may zoom. Enough to place a dimension
 * endpoint on a fillet in a 1568px capture; past it the image is interpolation. */
export const MAX_ZOOM = 8;

export function panBy(view: ViewTransform, dx: number, dy: number): ViewTransform {
  return { scale: view.scale, tx: view.tx + dx, ty: view.ty + dy };
}

/** Zoom by `factor` about `anchor`, a screen point that stays where it is.
 * Anchoring on the gesture rather than the viewport centre is what makes the
 * image feel attached to the fingers. */
export function zoomAt(view: ViewTransform, anchor: Point, factor: number): ViewTransform {
  if (!Number.isFinite(factor) || factor <= 0 || view.scale <= 0) return view;
  const scale = view.scale * factor;
  const at = toImage(view, anchor);
  return { scale, tx: anchor.x - at.x * scale, ty: anchor.y - at.y * scale };
}

/** A two-finger gesture reduced to what the transform needs. Screen pixels. */
export interface Gesture {
  readonly mid: Point;
  readonly spread: number;
}

/** The view after a pinch moved `from` to `to`. Zoom and pan in one step: the
 * image point under the gesture's midpoint stays under it, and the scale
 * follows the spread. Apply it incrementally, sample to sample, so a clamped
 * view becomes the base for the next move rather than being recomputed away. */
export function applyGesture(view: ViewTransform, from: Gesture, to: Gesture): ViewTransform {
  if (view.scale <= 0) return view;
  const factor = from.spread > 0 && to.spread > 0 ? to.spread / from.spread : 1;
  const scale = view.scale * factor;
  const at = toImage(view, from.mid);
  return { scale, tx: to.mid.x - at.x * scale, ty: to.mid.y - at.y * scale };
}

/** One axis of the pan clamp: centre the image when it is smaller than the
 * viewport, otherwise keep its edges outside the viewport's. */
function clampAxis(t: number, span: number, viewportSpan: number): number {
  if (span <= viewportSpan) return (viewportSpan - span) / 2;
  return Math.min(0, Math.max(viewportSpan - span, t));
}

/** Keep a view usable: never zoomed out past the contain-fit, never further in
 * than `MAX_ZOOM` times it, and never panned until the image has left the
 * screen. Every view the user produces goes through here. */
export function clampView(view: ViewTransform, image: Size, viewport: Size): ViewTransform {
  const fit = fitScale(image, viewport);
  if (fit <= 0) return identityView();
  if (view.scale <= 0 || !Number.isFinite(view.tx) || !Number.isFinite(view.ty)) {
    return fitView(image, viewport);
  }

  const scale = Math.min(Math.max(view.scale, fit), fit * MAX_ZOOM);
  let { tx, ty } = view;
  if (scale !== view.scale) {
    // Re-anchor on the viewport centre, so hitting the zoom limit holds what
    // the user was looking at instead of sliding it out from under them.
    const centre = { x: viewport.width / 2, y: viewport.height / 2 };
    const at = toImage(view, centre);
    tx = centre.x - at.x * scale;
    ty = centre.y - at.y * scale;
  }
  return {
    scale,
    tx: clampAxis(tx, image.width * scale, viewport.width),
    ty: clampAxis(ty, image.height * scale, viewport.height),
  };
}

type Ctx2D = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;

/** Drawn on top of the image and the ink, in SCREEN pixels rather than image
 * space, with the view transform passed in so it can place its own points.
 *
 * That is what dimension chrome needs: a handle is a tap target and a label is
 * text, so both have to be a fixed size on the surface they're drawn on rather
 * than scaling with the image. `chrome` is that surface's size relative to the
 * on-screen canvas -- 1 while drawing, larger when flattening to a PNG bigger
 * than the canvas, so the marks stay proportional instead of shrinking into
 * illegibility in the image Claude actually receives.
 *
 * A hook rather than a `dimensions` field on the scene: canvas.ts owns the
 * transform and the render loop, and knows nothing about what a dimension is. */
export type Overlay = (ctx: Ctx2D, view: ViewTransform, chrome: number) => void;

/** Everything that gets rendered, and everything that gets flattened. */
export interface Scene {
  image: ImageBitmap | null;
  strokes: Stroke[];
  overlay: Overlay | null;
}

export function newScene(): Scene {
  return { image: null, strokes: [], overlay: null };
}

export class CanvasView {
  readonly scene: Scene = newScene();
  view: ViewTransform = identityView();

  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;
  private viewport: Size = { width: 0, height: 0 };
  private dpr = 1;
  private frame: number | null = null;
  /** True while `view` is the contain-fit. A resize then re-fits; once the user
   * has zoomed it clamps instead, since re-fitting would throw away a transform
   * they chose because the tablet rotated. */
  private fitted = true;

  constructor(canvas: HTMLCanvasElement) {
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("2D canvas context unavailable");
    this.canvas = canvas;
    this.ctx = ctx;
  }

  /** Replace the base image. Ink is the caller's business -- it decides
   * whether a new image means a new drawing. */
  setImage(image: ImageBitmap | null): void {
    this.scene.image = image;
    this.fitted = true;
    this.refit();
    this.requestRender();
  }

  /** Match the backing store to the element's CSS size at devicePixelRatio,
   * then re-fit or re-clamp. Call from a ResizeObserver. */
  resize(): void {
    const dpr = window.devicePixelRatio || 1;
    const width = this.canvas.clientWidth;
    const height = this.canvas.clientHeight;
    if (width <= 0 || height <= 0) return;

    this.dpr = dpr;
    this.viewport = { width, height };
    const backingWidth = Math.round(width * dpr);
    const backingHeight = Math.round(height * dpr);
    if (this.canvas.width !== backingWidth || this.canvas.height !== backingHeight) {
      this.canvas.width = backingWidth;
      this.canvas.height = backingHeight;
    }
    if (this.fitted) this.refit();
    else this.setView(this.view);
    this.requestRender();
  }

  /** True once a gesture has taken the view off the contain-fit. */
  get zoomed(): boolean {
    return !this.fitted;
  }

  /** The one way the view is changed from outside: clamped on the way in, so
   * no gesture can leave the image off-screen or scaled into mush. */
  setView(next: ViewTransform): void {
    const image = this.imageSize();
    if (!image) return;
    const fit = fitScale(image, this.viewport);
    this.view = clampView(next, image, this.viewport);
    // A view clamped back onto the fit scale is the fit, since `clampView`
    // centres both axes there. No fit at all (no viewport yet) counts as
    // fitted, so nothing offers to undo a zoom that never happened.
    this.fitted = fit <= 0 || this.view.scale <= fit * (1 + 1e-9);
    this.requestRender();
  }

  /** Back to the contain-fit. */
  fit(): void {
    this.fitted = true;
    this.refit();
    this.requestRender();
  }

  /** Client (viewport) coordinates -> canvas-relative screen pixels. */
  screenFromClient(clientX: number, clientY: number): Point {
    const rect = this.canvas.getBoundingClientRect();
    return { x: clientX - rect.left, y: clientY - rect.top };
  }

  /** Client (viewport) coordinates -> image space. The one conversion the
   * input path is allowed to do. */
  imageFromClient(clientX: number, clientY: number): Point {
    return toImage(this.view, this.screenFromClient(clientX, clientY));
  }

  /** Coalesce renders onto the frame: a 120Hz pen delivers several samples per
   * frame and each one asks to redraw. */
  requestRender(): void {
    if (this.frame !== null) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = null;
      this.render();
    });
  }

  render(): void {
    const { ctx, canvas } = this;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const { scale, tx, ty } = this.view;
    const d = this.dpr;
    ctx.setTransform(scale * d, 0, 0, scale * d, tx * d, ty * d);

    const { image, strokes, overlay } = this.scene;
    if (image) ctx.drawImage(image, 0, 0);
    for (const stroke of strokes) drawStroke(ctx, stroke);

    if (overlay) {
      // Back to CSS pixels (times the device ratio, which is about display
      // density, not layout) so the overlay's sizes mean what they say.
      ctx.setTransform(d, 0, 0, d, 0, 0);
      overlay(ctx, this.view, 1);
    }
  }

  private imageSize(): Size | null {
    const { image } = this.scene;
    return image ? { width: image.width, height: image.height } : null;
  }

  private refit(): void {
    const image = this.imageSize();
    this.view = image ? fitView(image, this.viewport) : identityView();
  }
}

/** The eraser's reach, drawn where the pen is while an erase gesture runs.
 * Screen space, like everything else an `Overlay` draws. */
export function drawEraserCursor(
  ctx: Ctx2D,
  view: ViewTransform,
  at: Point,
  radius: number,
): void {
  const p = toScreen(view, at);
  ctx.save();
  ctx.beginPath();
  ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(230,233,238,.12)";
  ctx.fill();
  ctx.strokeStyle = "rgba(230,233,238,.7)";
  ctx.lineWidth = 1.5;
  ctx.stroke();
  ctx.restore();
}

/** The long edge the flattened PNG is capped at.
 *
 * Roughly where the API's image blocks cap out anyway, and a 12MP phone photo
 * is megabytes of wifi upload that buys nothing above it. */
export const MAX_FLAT_EDGE = 1568;

/** Pure: the output size for `size` scaled to fit a `maxEdge` long edge.
 *
 * Under the cap the size is returned untouched -- upscaling a small photo
 * would invent detail and cost bytes. Over it, the long edge lands on exactly
 * `maxEdge` and the short edge is rounded, so the result is always integral
 * (a fractional canvas size is silently truncated by the browser, which is how
 * an off-by-one aspect error would sneak in). */
export function fitWithin(size: Size, maxEdge = MAX_FLAT_EDGE): Size {
  const { width, height } = size;
  const longEdge = Math.max(width, height);
  if (longEdge <= maxEdge || longEdge <= 0) return { width, height };
  if (width >= height) {
    return { width: maxEdge, height: Math.max(1, Math.round((height * maxEdge) / width)) };
  }
  return { width: Math.max(1, Math.round((width * maxEdge) / height)), height: maxEdge };
}

/** The canvas width the overlay's fixed sizes were chosen against. A flatten
 * wider than this scales its chrome up in proportion, so a dimension label is
 * the same fraction of the picture Claude receives as it was of the one the
 * user drew on. */
const CHROME_REFERENCE_WIDTH = 1000;

/** A canvas to compose into, plus how to get a PNG out of it. OffscreenCanvas
 * where it exists (no DOM node, no layout), a detached <canvas> otherwise --
 * Safari only grew OffscreenCanvas 2D recently and this has to work on the
 * tablet in front of the user, not the newest one. */
function composeTarget(size: Size): {
  ctx: Ctx2D;
  toBlob: () => Promise<Blob>;
} {
  if (typeof OffscreenCanvas !== "undefined") {
    const canvas = new OffscreenCanvas(size.width, size.height);
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("2D canvas context unavailable");
    return { ctx, toBlob: () => canvas.convertToBlob({ type: "image/png" }) };
  }
  const canvas = document.createElement("canvas");
  canvas.width = size.width;
  canvas.height = size.height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("2D canvas context unavailable");
  return {
    ctx,
    toBlob: () =>
      new Promise<Blob>((resolve, reject) => {
        canvas.toBlob(
          (blob) => (blob ? resolve(blob) : reject(new Error("PNG encoding failed"))),
          "image/png",
        );
      }),
  };
}

/** Flatten the base image plus its ink into a single PNG, downscaled to a
 * `maxEdge` long edge.
 *
 * Rendered from the SCENE, not from the on-screen canvas: the screen is at
 * whatever size the device's window happens to be, and the flattened image has
 * to be the image the user marked up, at its own resolution, so that the
 * annotation document's normalized coordinates mean the same thing on both
 * sides of the wire.
 *
 * The blob this returns is what Send POSTs to /api/upload. */
export async function flattenToPng(scene: Scene, maxEdge = MAX_FLAT_EDGE): Promise<Blob> {
  const { image } = scene;
  if (!image) throw new Error("nothing to flatten -- no image loaded");

  const source = { width: image.width, height: image.height };
  const out = fitWithin(source, maxEdge);
  const { ctx, toBlob } = composeTarget(out);

  ctx.setTransform(out.width / source.width, 0, 0, out.height / source.height, 0, 0);
  ctx.drawImage(image, 0, 0);
  for (const stroke of scene.strokes) drawStroke(ctx, stroke);

  if (scene.overlay) {
    // The overlay draws in the OUTPUT's pixels, so its view transform is the
    // downscale itself -- image space into the flattened image. The dimensions
    // have to be in this PNG: it is the picture Claude sees, and a measurement
    // that only existed on the tablet would leave the JSON pointing at a mark
    // that isn't there.
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    const scale = out.width / source.width;
    scene.overlay(ctx, { scale, tx: 0, ty: 0 }, Math.max(1, out.width / CHROME_REFERENCE_WIDTH));
  }
  return toBlob();
}
