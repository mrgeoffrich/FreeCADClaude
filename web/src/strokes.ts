// SPDX-License-Identifier: LGPL-2.1-or-later
// Freehand ink: a thin wrapper over perfect-freehand, the one runtime
// dependency.
//
// A stroke stores its points in IMAGE space, never screen space -- the same
// rule the annotation document follows -- so ink survives a resize, a device
// rotation, and (later) a pinch-zoom without being re-fitted. Its `size` is
// likewise an image-space width, chosen at stroke start from the view scale so
// the ink looks the same thickness under the pen whatever the image's
// resolution.
//
// Strokes are deliberately NOT serialized into the annotation document: Claude
// sees them in the flattened PNG, and a point list would be tokens spent on
// something already visible.
import { getStroke } from "perfect-freehand";
import type { Vec2 } from "perfect-freehand";

/** A sampled point of a stroke, in image coordinates. */
export interface InkPoint {
  readonly x: number;
  readonly y: number;
  /** 0..1 as reported by the pointer; see `simulatePressure`. */
  readonly pressure: number;
}

export interface Stroke {
  readonly color: string;
  /** Base diameter, in image pixels. */
  readonly size: number;
  /** True when the device reports no real force, so perfect-freehand should
   * infer it from velocity instead. A mouse and most touchscreens report a
   * constant 0.5, which would otherwise render as a dead uniform line. */
  readonly simulatePressure: boolean;
  readonly points: InkPoint[];
  /** Set by `finishStroke`; perfect-freehand caps the tail differently once it
   * knows no more points are coming. */
  completed: boolean;
  /** Memoized outline; invalidated by `addPoint`. Recomputing every stroke's
   * outline on every frame is the obvious way to make a 120Hz pen feel slow. */
  outline: Vec2[] | null;
}

/** The ink colour, matching the mockup's `--ink`. */
export const INK_COLOR = "#ff5d5d";

/** Pen width in CSS pixels. Converted to image space at stroke start. */
export const PEN_CSS_WIDTH = 4;

export function newStroke(size: number, simulatePressure: boolean, color = INK_COLOR): Stroke {
  return { color, size, simulatePressure, points: [], completed: false, outline: null };
}

export function addPoint(stroke: Stroke, point: InkPoint): void {
  stroke.points.push(point);
  stroke.outline = null;
}

export function finishStroke(stroke: Stroke): void {
  stroke.completed = true;
  stroke.outline = null;
}

/** The polygon perfect-freehand computes around the sampled points. */
export function outlineOf(stroke: Stroke): Vec2[] {
  if (stroke.outline === null) {
    stroke.outline = getStroke(stroke.points, {
      size: stroke.size,
      // `thinning` IS the pressure→width mapping: e.pressure, recorded per
      // sample, scales the diameter by up to ±60%.
      thinning: 0.6,
      smoothing: 0.5,
      streamline: 0.4,
      simulatePressure: stroke.simulatePressure,
      last: stroke.completed,
    });
  }
  return stroke.outline;
}

type Ctx2D = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;

/** Fill a stroke's outline into a context whose transform is already image
 * space. The path is traced with quadratics through the outline's midpoints --
 * the standard perfect-freehand rendering, which is what keeps the polygon
 * from looking faceted. */
export function drawStroke(ctx: Ctx2D, stroke: Stroke): void {
  const outline = outlineOf(stroke);
  if (outline.length === 0) return;

  ctx.beginPath();
  const [first] = outline;
  if (!first) return;
  ctx.moveTo(first[0], first[1]);
  for (let i = 1; i < outline.length; i += 1) {
    const a = outline[i]!;
    const b = outline[(i + 1) % outline.length]!;
    ctx.quadraticCurveTo(a[0], a[1], (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
  }
  ctx.closePath();
  ctx.fillStyle = stroke.color;
  ctx.fill();
}
