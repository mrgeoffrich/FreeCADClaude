// SPDX-License-Identifier: LGPL-2.1-or-later
// Dimensions: the annotations that carry a number.
//
// A dimension is two points and (optionally) a target. It is the only mark the
// app serializes -- freehand ink stays in the picture, where Claude can already
// see it, while a dimension carries what the picture can't: how long something
// is, and how long the user wants it to be.
//
// Endpoints are stored in IMAGE space like everything else, and every
// interaction converts through the view transform. HIT-TESTING IS DONE IN
// SCREEN SPACE ON PURPOSE: the tolerance is a fingertip, which is a fixed
// number of CSS pixels no matter how far the image is zoomed. Comparing image
// distances against a constant would make handles easy to grab on a zoomed-in
// view and impossible on a zoomed-out one -- and since v1's transform is
// already a real contain-fit (not an identity), that is a bug you would hit on
// the first tablet, not a hypothetical about the deferred pinch-zoom.
import { toScreen, type Point, type ViewTransform } from "./canvas";
import { dimensionLabel, type Scale } from "./scale";

export interface Dimension {
  readonly id: string;
  a: Point;
  b: Point;
  /** What the user wants it to be, in mm. Null until they type one -- "this is
   * 24.3mm" and "make this 30mm" are different facts and the document keeps
   * them apart. */
  targetMm: number | null;
  note: string;
}

/** A dimension being placed: first point down, second following the pen. */
export interface DimensionDraft {
  readonly a: Point;
  readonly b: Point;
}

/** Which end of which dimension a gesture grabbed. */
export interface EndpointRef {
  readonly dimension: Dimension;
  readonly end: "a" | "b";
}

/** How close a tap has to land to grab an endpoint, in CSS pixels. Generous:
 * the handle is drawn at 7px and a pen tip covers rather more than that. */
export const HIT_CSS = 22;

/** How close a new point has to land to snap onto an existing endpoint, in CSS
 * pixels. Smaller than the grab radius so snapping doesn't hijack a
 * deliberate placement next to an existing one. */
export const SNAP_CSS = 16;

export function newDimension(id: string, a: Point, b: Point): Dimension {
  return { id, a, b, targetMm: null, note: "" };
}

/** The document's `d1`, `d2`, ... ids. Sequential rather than random: they
 * appear in the JSON Claude reads, and "d2" is something a human can say back
 * to it. */
export function dimensionId(seq: number): string {
  return `d${seq}`;
}

export function endpointOf(ref: EndpointRef): Point {
  return ref.end === "a" ? ref.dimension.a : ref.dimension.b;
}

export function setEndpoint(ref: EndpointRef, p: Point): void {
  if (ref.end === "a") ref.dimension.a = p;
  else ref.dimension.b = p;
}

function screenDistance(view: ViewTransform, a: Point, b: Point): number {
  const p = toScreen(view, a);
  const q = toScreen(view, b);
  return Math.hypot(q.x - p.x, q.y - p.y);
}

function endpoints(items: readonly Dimension[]): EndpointRef[] {
  const out: EndpointRef[] = [];
  for (const dimension of items) {
    out.push({ dimension, end: "a" }, { dimension, end: "b" });
  }
  return out;
}

function same(a: EndpointRef, b: EndpointRef | null | undefined): boolean {
  return b !== null && b !== undefined && a.dimension === b.dimension && a.end === b.end;
}

/** The endpoint a tap at image-space `p` grabs, nearest first, or null.
 *
 * Nearest rather than first-found: two endpoints snapped together are a normal
 * result of `snap`, and picking whichever happened to be created first would
 * make one of them unreachable. */
export function endpointAt(
  items: readonly Dimension[],
  view: ViewTransform,
  p: Point,
  tolerance = HIT_CSS,
): EndpointRef | null {
  let best: EndpointRef | null = null;
  let bestDistance = tolerance;
  for (const ref of endpoints(items)) {
    const distance = screenDistance(view, endpointOf(ref), p);
    if (distance <= bestDistance) {
      best = ref;
      bestDistance = distance;
    }
  }
  return best;
}

/** `p`, moved onto a nearby existing endpoint if there is one.
 *
 * This is what makes a chain of dimensions share exact corners: two dimensions
 * from the same feature edge should start from the same coordinate, not from
 * two taps a few pixels apart, or the numbers disagree by the width of the
 * user's pen. `skip` is the endpoint currently being dragged -- an endpoint
 * must not snap to itself, and dragging one onto its own partner is the user's
 * business. */
export function snap(
  items: readonly Dimension[],
  view: ViewTransform,
  p: Point,
  tolerance = SNAP_CSS,
  skip?: EndpointRef | null,
): Point {
  let best: Point | null = null;
  let bestDistance = tolerance;
  for (const ref of endpoints(items)) {
    if (same(ref, skip)) continue;
    const target = endpointOf(ref);
    const distance = screenDistance(view, target, p);
    if (distance <= bestDistance) {
      best = target;
      bestDistance = distance;
    }
  }
  return best ?? p;
}

// ── rendering ───────────────────────────────────────────────────────────────
// Drawn in SCREEN space (the context transform is CSS pixels, or output pixels
// when flattening), converting each stored point through the view: witness
// lines, ticks, handles and the label pill are chrome, and chrome that scaled
// with the image would be unreadable on a zoomed-out view and gigantic on a
// zoomed-in one. `chrome` multiplies those constants so the flattened PNG --
// which is larger than the on-screen canvas -- gets proportionally sized marks
// rather than a 15px caption on a 1568px image.

type Ctx2D = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;

/** The dimension colour, matching the mockup's `--dim`. */
export const DIM_COLOR = "#ffc14d";
/** The calibration colour, matching the mockup's `--accent`. */
export const CAL_COLOR = "#6ea8fe";
/** The pill/handle fill, matching the mockup's `--bg`. */
const PILL_FILL = "#14161a";

const WITNESS_HALF = 13;
const TICK = 8;
const HANDLE_R = 7;
const LABEL_FONT = 15;
const LABEL_PAD = 12;
const LABEL_GAP = 14;

function roundedRect(ctx: Ctx2D, x: number, y: number, w: number, h: number, r: number): void {
  // Traced by hand rather than with `ctx.roundRect`: that landed in Safari only
  // recently and this has to draw on the tablet in front of the user.
  const radius = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + w, y, x + w, y + h, radius);
  ctx.arcTo(x + w, y + h, x, y + h, radius);
  ctx.arcTo(x, y + h, x, y, radius);
  ctx.arcTo(x, y, x + w, y, radius);
  ctx.closePath();
}

function pill(ctx: Ctx2D, cx: number, cy: number, text: string, chrome: number, color: string): void {
  ctx.font = `600 ${LABEL_FONT * chrome}px -apple-system, "Segoe UI", Roboto, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const width = ctx.measureText(text).width + LABEL_PAD * 2 * chrome;
  const height = (LABEL_FONT + 15) * chrome;
  roundedRect(ctx, cx - width / 2, cy - height / 2, width, height, height / 2);
  ctx.fillStyle = PILL_FILL;
  ctx.fill();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5 * chrome;
  ctx.stroke();
  ctx.fillStyle = color;
  ctx.fillText(text, cx, cy);
}

function line(ctx: Ctx2D, ax: number, ay: number, bx: number, by: number): void {
  ctx.beginPath();
  ctx.moveTo(ax, ay);
  ctx.lineTo(bx, by);
  ctx.stroke();
}

function handle(ctx: Ctx2D, x: number, y: number, r: number, color: string): void {
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fillStyle = PILL_FILL;
  ctx.fill();
  ctx.strokeStyle = color;
  ctx.stroke();
}

/** One placed dimension, per the mockup: witness lines out of each endpoint,
 * the dimension line between them, inward ticks, round handles, and the value
 * pill below the middle. */
function drawOne(ctx: Ctx2D, view: ViewTransform, dim: Dimension, label: string, chrome: number): void {
  const a = toScreen(view, dim.a);
  const b = toScreen(view, dim.b);
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const length = Math.hypot(dx, dy) || 1;
  const ux = dx / length;
  const uy = dy / length;
  // Perpendicular, for the witness lines and for pushing the label clear of
  // the line whichever way the dimension runs.
  const nx = -uy;
  const ny = ux;

  ctx.strokeStyle = DIM_COLOR;
  ctx.lineWidth = 2.5 * chrome;
  ctx.lineCap = "round";

  const witness = WITNESS_HALF * chrome;
  line(ctx, a.x - nx * witness, a.y - ny * witness, a.x + nx * witness, a.y + ny * witness);
  line(ctx, b.x - nx * witness, b.y - ny * witness, b.x + nx * witness, b.y + ny * witness);
  line(ctx, a.x, a.y, b.x, b.y);

  // Ticks: short chevrons pointing outward from each end, so the dimension
  // reads as measuring BETWEEN the handles rather than just joining them.
  const tick = TICK * chrome;
  for (const [p, dir] of [
    [a, 1],
    [b, -1],
  ] as const) {
    const tipX = p.x + ux * dir * tick;
    const tipY = p.y + uy * dir * tick;
    line(ctx, tipX + nx * tick * 0.55, tipY + ny * tick * 0.55, p.x, p.y);
    line(ctx, tipX - nx * tick * 0.55, tipY - ny * tick * 0.55, p.x, p.y);
  }

  handle(ctx, a.x, a.y, HANDLE_R * chrome, DIM_COLOR);
  handle(ctx, b.x, b.y, HANDLE_R * chrome, DIM_COLOR);

  const gap = (LABEL_GAP + LABEL_FONT) * chrome;
  pill(ctx, (a.x + b.x) / 2 + nx * gap, (a.y + b.y) / 2 + ny * gap, label, chrome, DIM_COLOR);
}

/** The dimension being placed: a dashed rubber band from the first point to
 * wherever the pen is, a target ring at the loose end, and the live
 * measurement -- so the user sees the number before committing to it. */
function drawDraft(ctx: Ctx2D, view: ViewTransform, draft: DimensionDraft, label: string, chrome: number): void {
  const a = toScreen(view, draft.a);
  const b = toScreen(view, draft.b);
  ctx.strokeStyle = DIM_COLOR;
  ctx.lineWidth = 2.5 * chrome;
  ctx.setLineDash([7 * chrome, 6 * chrome]);
  line(ctx, a.x, a.y, b.x, b.y);
  ctx.setLineDash([]);

  handle(ctx, a.x, a.y, 8 * chrome, DIM_COLOR);
  ctx.beginPath();
  ctx.arc(b.x, b.y, 14 * chrome, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(255,193,77,.18)";
  ctx.fill();
  ctx.lineWidth = 1.5 * chrome;
  ctx.stroke();

  pill(ctx, b.x, b.y - 32 * chrome, label, chrome, DIM_COLOR);
}

/** The two calibration taps, in the accent colour rather than the dimension
 * one: they are not a measurement, they are the thing that makes measurements
 * possible. */
function drawCalibration(ctx: Ctx2D, view: ViewTransform, pair: DimensionDraft, chrome: number): void {
  const a = toScreen(view, pair.a);
  const b = toScreen(view, pair.b);
  ctx.strokeStyle = CAL_COLOR;
  ctx.lineWidth = 2.5 * chrome;
  ctx.setLineDash([8 * chrome, 6 * chrome]);
  line(ctx, a.x, a.y, b.x, b.y);
  ctx.setLineDash([]);
  handle(ctx, a.x, a.y, 9 * chrome, CAL_COLOR);
  handle(ctx, b.x, b.y, 9 * chrome, CAL_COLOR);
}

export interface DimensionLayer {
  readonly items: readonly Dimension[];
  readonly draft: DimensionDraft | null;
  /** The two points of an in-progress calibration, drawn in accent colour. */
  readonly calibration: DimensionDraft | null;
  readonly scale: Scale;
}

/** Draw everything dimension-shaped. `ctx` must already be in screen (CSS or
 * output) pixels; `chrome` scales the fixed sizes for a larger surface. */
export function drawDimensions(
  ctx: Ctx2D,
  view: ViewTransform,
  layer: DimensionLayer,
  chrome = 1,
): void {
  ctx.save();
  for (const dim of layer.items) {
    drawOne(ctx, view, dim, dimensionLabel(layer.scale, dim.a, dim.b, dim.targetMm), chrome);
  }
  if (layer.draft) {
    drawDraft(ctx, view, layer.draft, dimensionLabel(layer.scale, layer.draft.a, layer.draft.b, null), chrome);
  }
  if (layer.calibration) drawCalibration(ctx, view, layer.calibration, chrome);
  ctx.restore();
}
