// SPDX-License-Identifier: LGPL-2.1-or-later
// What a pixel is worth, and how sure we are about it.
//
// A picture carries no scale. That is the whole reason the payload is an image
// AND a document: a circled region tells Claude WHERE, only a number tells it
// HOW MUCH. This module is where that number comes from, and it comes from
// exactly two places:
//
//   a FreeCAD capture   mm/px derived from the orthographic camera, published
//                       in the capture metadata (render.py `_capture_optics`).
//   a device photo      two taps and a typed real length, here.
//
// `confidence` travels with the number rather than being assumed by whoever
// reads it, because the three cases need different behaviour from Claude:
//
//   exact        an axis-aligned ortho capture. Distances in the projection
//                plane are true; model to them.
//   approximate  either an oblique camera (foreshortened by an unknown amount)
//                or a user-calibrated photo (right along the calibration line,
//                wrong everywhere else). A number worth reading, not machining.
//   none         no scale at all. THE LOAD-BEARING RULE: at `none` a distance
//                is a pixel count and must never be rendered, formatted or
//                serialized as millimetres. `measureMm` returns null and
//                `measureLabel` says "348 px" -- there is no code path here
//                that turns a pixel into a mm without a scale, which is what
//                stops a reading off a napkin photo from arriving as a
//                dimension.
//
// An oblique capture DOWNGRADES rather than dropping its number, deliberately:
// Claude has to be able to tell "no measurement" from "a measurement you
// shouldn't machine to", and omitting the field makes those two identical.

import type { Point } from "./canvas";

export type Confidence = "exact" | "approximate" | "none";

/** An image's pixel dimensions. Normalized document coordinates are converted
 * through this, and `mmPerPx` is expressed per pixel OF THIS GRID. */
export interface PixelSize {
  readonly width: number;
  readonly height: number;
}

export interface Scale {
  /** Millimetres per pixel of the loaded image, or null when unknown. */
  readonly mmPerPx: number | null;
  /** The image `mmPerPx` is expressed against -- the bitmap actually loaded,
   * not whatever size it was published at (see `captureScale`). */
  readonly pixels: PixelSize;
  readonly confidence: Confidence;
  /** Which world plane distances are true in, verbatim from the capture (or
   * the calibration's own caveat). Null when there is no scale to qualify. */
  readonly plane: string | null;
  readonly kind: "capture" | "calibration" | "none";
}

/** Only true when the camera is looking down a world axis; a photo is
 * calibrated along one line and is never better than this. */
const CALIBRATION_PLANE =
  "calibrated from two points the user tapped on a photo; only true along that " +
  "line, and only if the subject was roughly flat-on to the camera";

export function noScale(pixels: PixelSize): Scale {
  return { mmPerPx: null, pixels, confidence: "none", plane: null, kind: "none" };
}

function isPositive(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

/** The scale a published capture carries, expressed against the bitmap that
 * was actually loaded.
 *
 * `meta` is parsed rather than typed-and-trusted: it crosses a version
 * boundary (a browser tab outlives a FreeCAD restart), and a malformed scale
 * has to read as "no scale", which is a state the UI already handles.
 *
 * Two rules are enforced here rather than left to the publisher:
 * - **The number is rescaled to the loaded image.** mm/px is published against
 *   the rendered size; if what we loaded is a different size, a pixel is worth
 *   proportionally more or less. Normally they are identical -- this is what
 *   keeps them from silently disagreeing if they ever aren't.
 * - **`exact` is refused when the camera says it isn't axis-aligned.**
 *   render.py already downgrades, so this is belt-and-braces on the one claim
 *   whose failure mode is a user machining to a foreshortened number.
 */
export function captureScale(meta: unknown, image: PixelSize): Scale {
  const root = record(meta);
  const scale = record(root?.scale);
  const mmPerPxAtPublish = scale?.mm_per_px;
  if (!root || !scale || !isPositive(mmPerPxAtPublish) || !isPositive(image.width)) {
    return noScale(image);
  }

  const published = record(root.image);
  const publishedWidth = published?.width;
  const ratio = isPositive(publishedWidth) ? publishedWidth / image.width : 1;

  const camera = record(root.camera);
  const declared = scale.confidence;
  const claimed: Confidence =
    declared === "exact" || declared === "approximate" ? declared : "approximate";
  const confidence: Confidence = camera?.axis_aligned === false ? "approximate" : claimed;

  return {
    mmPerPx: mmPerPxAtPublish * ratio,
    pixels: image,
    confidence,
    plane: typeof scale.plane === "string" ? scale.plane : null,
    kind: "capture",
  };
}

/** Two tapped points plus the real distance between them -> mm/px, or null if
 * the pair can't define one.
 *
 * Always `approximate`, never `exact`, whatever the user types: the number is
 * right along the calibration line and wrong everywhere else the moment the
 * photo was taken at an angle, and there is nothing in the image that can tell
 * us it wasn't. */
export function calibrationScale(
  a: Point,
  b: Point,
  realMm: number,
  pixels: PixelSize,
): Scale | null {
  const px = Math.hypot(b.x - a.x, b.y - a.y);
  // A sub-pixel span would divide the typed length by ~0 and hand back a scale
  // that reads as astronomically fine rather than as the mis-tap it is.
  if (!Number.isFinite(realMm) || realMm <= 0 || px < 1) return null;
  return {
    mmPerPx: realMm / px,
    pixels,
    confidence: "approximate",
    plane: CALIBRATION_PLANE,
    kind: "calibration",
  };
}

/** Straight-line distance between two image-space points, in image pixels. */
export function pixelDistance(a: Point, b: Point): number {
  return Math.hypot(b.x - a.x, b.y - a.y);
}

/** The distance in millimetres, or **null when there is no scale** -- the one
 * gate that keeps a pixel count from being quoted as a length. */
export function measureMm(scale: Scale, a: Point, b: Point): number | null {
  if (scale.mmPerPx === null) return null;
  return pixelDistance(a, b) * scale.mmPerPx;
}

/** Round a millimetre value to the precision the measurement can support.
 *
 * A tap is worth a few pixels and a pixel is a few hundredths of a millimetre
 * on a typical capture, so more digits than this is invented precision -- and
 * invented precision is what makes a reading look like a specification. Used
 * by both the on-screen label and the serialized document, so the number
 * Claude reads is the number the user saw. */
export function roundMm(mm: number): number {
  const decimals = Math.abs(mm) >= 10 ? 1 : 2;
  const factor = 10 ** decimals;
  return Math.round(mm * factor) / factor;
}

/** "24.3 mm". Trailing zeros are dropped by Number->string, so 30 reads as
 * "30 mm" and not "30.0 mm", matching the mockup. */
export function formatMm(mm: number): string {
  return `${roundMm(mm)} mm`;
}

/** "348 px" -- what a measurement reads as when there is no scale. */
export function formatPixels(px: number): string {
  return `${Math.round(px)} px`;
}

/** What a dimension's measured length reads as: millimetres when we have a
 * scale, pixels when we don't. Never mm at `none`. */
export function measureLabel(scale: Scale, a: Point, b: Point): string {
  const mm = measureMm(scale, a, b);
  return mm === null ? formatPixels(pixelDistance(a, b)) : formatMm(mm);
}

/** The label on a placed dimension: what it measures, and what the user asked
 * for if they typed a target ("24.3 → 30 mm").
 *
 * A target is the user's own instruction, so it is quoted in mm even with no
 * scale -- "make this 30mm" needs no measurement to be meaningful. Only the
 * MEASURED half is gated, and at `none` it reads "348 px → 30 mm", which says
 * plainly that the two numbers are different kinds of fact. */
export function dimensionLabel(
  scale: Scale,
  a: Point,
  b: Point,
  targetMm: number | null,
): string {
  const mm = measureMm(scale, a, b);
  // With a scale the unit is written once, at the end, so the pair reads as
  // one quantity ("24.3 → 30 mm"). Without one the units genuinely differ and
  // both have to be spelled out.
  const measured = mm === null ? formatPixels(pixelDistance(a, b)) : `${roundMm(mm)}`;
  if (targetMm === null || !Number.isFinite(targetMm)) {
    return mm === null ? measured : `${measured} mm`;
  }
  return `${measured} → ${roundMm(targetMm)} mm`;
}

/** The status bar's mm/px readout, or "" when there is nothing to say (the
 * confidence badge already carries "no scale"). Two significant figures: this
 * is an at-a-glance sanity check, not a number anyone computes with. */
export function scaleStatusText(scale: Scale): string {
  if (scale.mmPerPx === null) return "";
  const value = Number(scale.mmPerPx.toPrecision(2));
  return `${value} mm/px`;
}
