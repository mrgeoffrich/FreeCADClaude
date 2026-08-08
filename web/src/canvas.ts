// SPDX-License-Identifier: LGPL-2.1-or-later
// The drawing surface: the view transform, the render loop, and flattening
// what's on screen down to a PNG.
//
// THE VIEW TRANSFORM IS THE POINT OF THIS FILE. Every coordinate the app
// stores -- ink points, and later dimension endpoints -- lives in IMAGE space
// and passes through `ViewTransform` at render and hit-test time. Nothing
// anywhere stores a screen pixel. Pinch-zoom is deliberately not in v1, but
// having the transform from day one is what makes adding the gesture a
// contained change (assign a new `view`) instead of a refactor of everything
// that touches a coordinate.
//
// In v1 the transform only ever holds the contain-fit of the image into the
// canvas, recomputed on resize; the user cannot change it. That is a real
// transform being exercised, not a hardcoded identity that would quietly rot.
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

/** Contain-fit `image` inside `viewport`, centred. */
export function fitView(image: Size, viewport: Size): ViewTransform {
  if (image.width <= 0 || image.height <= 0 || viewport.width <= 0 || viewport.height <= 0) {
    return identityView();
  }
  const scale = Math.min(viewport.width / image.width, viewport.height / image.height);
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
    this.refit();
    this.requestRender();
  }

  /** Match the backing store to the element's CSS size at devicePixelRatio,
   * then re-fit. Call from a ResizeObserver. */
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
    this.refit();
    this.requestRender();
  }

  /** Client (viewport) coordinates -> image space. The one conversion the
   * input path is allowed to do. */
  imageFromClient(clientX: number, clientY: number): Point {
    const rect = this.canvas.getBoundingClientRect();
    return toImage(this.view, { x: clientX - rect.left, y: clientY - rect.top });
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

  private refit(): void {
    const { image } = this.scene;
    this.view = image
      ? fitView({ width: image.width, height: image.height }, this.viewport)
      : identityView();
  }
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
