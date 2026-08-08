// SPDX-License-Identifier: LGPL-2.1-or-later
// The annotation document: the half of the payload that carries numbers.
//
// The picture carries what JSON shouldn't -- shape, gesture, emphasis, "this
// bit here". This carries what the picture can't -- numbers, identity, intent.
// Four decisions hold that split up, and none of them should be undone
// casually:
//
// - **Coordinates are normalized 0..1 in image space**, never screen pixels.
//   The document then survives a resize, a device rotation, a zoom level and
//   the downscale the flatten applies on the way out (`fitWithin`): the PNG
//   Claude sees may be 1568px wide when the canvas was 900, and 0.31 means the
//   same place in both.
// - **Freehand strokes are not serialized.** They are already visible in the
//   flattened image; a point list would be tokens spent on something Claude
//   can see.
// - **`measured_mm` and `target_mm` are different facts.** "This is 24.3mm" is
//   information; "make this 30mm" is an instruction. Keeping both lets Claude
//   see the delta, and lets it tell a dimension the user was reading off from
//   one they were asking for. `target_mm` is null until they type one.
// - **`snapped_to` is reserved and always null in v1.** Geometry ray-picking
//   ("Vertex3 of Pad001") is a later phase; the field exists now so that phase
//   is additive rather than a schema change.
//
// `version` is the schema's, and it is written by the device and read by
// whoever gets the file. The client is not the only reader -- the document is
// stored beside the PNG and included verbatim in `read_device_image`, so a
// browser tab and a FreeCAD install can be different vintages.
import type { Point } from "./canvas";
import type { Dimension } from "./dimensions";
import { measureMm, roundMm, type PixelSize, type Scale } from "./scale";

/** The schema version written into every document. Bump only for a change a
 * reader cannot ignore. */
export const DOC_VERSION = 1;

/** The filename the PNG is attached under. Cosmetic on the wire -- the server
 * generates its own name in one fixed folder, and never consults this -- but
 * the document names the image it travels with, so the two agree. */
export const IMAGE_NAME = "annotation.png";

/** `[x, y]`, each 0..1 of the image's width/height. An array rather than an
 * object because it appears once per annotation and reads as a coordinate. */
export type NormPoint = readonly [number, number];

export interface SerializedScale {
  readonly mm_per_px: number;
  readonly confidence: "exact" | "approximate";
  readonly plane: string | null;
}

export interface SerializedSource {
  readonly kind: string;
  /** The published capture's id, when the marks were drawn on one. This is how
   * `read_device_image` finds the camera angle and world extents to replay. */
  readonly id?: string;
  readonly document?: string;
  readonly camera?: {
    readonly projection: string | null;
    readonly view: string | null;
    readonly axis_aligned: boolean | null;
  };
  /** Null when the image has no scale -- an explicit "you cannot read a
   * millimetre off this", rather than an absent field that reads as an
   * oversight. */
  readonly scale: SerializedScale | null;
}

export interface SerializedDimension {
  readonly id: string;
  readonly type: "dimension";
  readonly a: NormPoint;
  readonly b: NormPoint;
  /** What it measures, in mm -- **null when the image has no scale**, in which
   * case the distance is a pixel ratio and must not be quoted in mm. */
  readonly measured_mm: number | null;
  /** What the user asked for. Null until they type one. */
  readonly target_mm: number | null;
  readonly note: string;
  /** Reserved for geometry picking; always null in v1. */
  readonly snapped_to: null;
}

export interface AnnotationDoc {
  readonly version: number;
  readonly image: string;
  readonly source: SerializedSource | null;
  readonly annotations: readonly SerializedDimension[];
  readonly caption: string;
}

/** What `buildDoc` needs to know about where the image came from.
 *
 * A flattened shape rather than the `Published` record itself, so this module
 * depends on neither `api.ts` nor the wire format -- `api.ts` imports
 * `AnnotationDoc` from here, and a cycle between the two would be one type
 * rename away from confusing. */
export interface DocSourceInput {
  readonly kind: string;
  readonly id?: string;
  readonly document?: string;
  readonly view?: string | null;
  readonly projection?: string | null;
  readonly axisAligned?: boolean | null;
}

export interface DocInput {
  readonly caption: string;
  readonly source: DocSourceInput | null;
  /** The active scale. Supplies both the pixel grid coordinates are
   * normalized against and the millimetres the measurements are quoted in, so
   * the two cannot be derived from different images. */
  readonly scale: Scale;
  readonly dimensions: readonly Dimension[];
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

/** Image-space point -> normalized. Clamped: placement already keeps points on
 * the image, so this is a guard against a coordinate that would read as
 * off-picture to whoever gets the JSON. Five decimals is a third of a pixel on
 * a 1568px image -- below what a pen tap resolves, and far above what the
 * rounding of `measured_mm` cares about. */
export function toNormalized(p: Point, size: PixelSize): NormPoint {
  const nx = size.width > 0 ? p.x / size.width : 0;
  const ny = size.height > 0 ? p.y / size.height : 0;
  return [Number(clamp01(nx).toFixed(5)), Number(clamp01(ny).toFixed(5))];
}

/** Normalized -> image-space, for whoever reads a document back. */
export function fromNormalized(n: NormPoint, size: PixelSize): Point {
  return { x: n[0] * size.width, y: n[1] * size.height };
}

function serializeScale(scale: Scale): SerializedScale | null {
  if (scale.mmPerPx === null || scale.confidence === "none") return null;
  return {
    // Six decimals: mm/px runs around 0.08 on a typical capture, so this is
    // ~1e-5 mm of resolution -- far below anything measurable, and short
    // enough that the number is readable in the JSON.
    mm_per_px: Number(scale.mmPerPx.toFixed(6)),
    confidence: scale.confidence,
    plane: scale.plane,
  };
}

function serializeSource(source: DocSourceInput | null, scale: Scale): SerializedSource | null {
  if (!source) return null;
  const out: SerializedSource = {
    kind: source.kind,
    ...(source.id ? { id: source.id } : {}),
    ...(source.document ? { document: source.document } : {}),
    ...(source.projection !== undefined ||
    source.view !== undefined ||
    source.axisAligned !== undefined
      ? {
          camera: {
            projection: source.projection ?? null,
            view: source.view ?? null,
            axis_aligned: source.axisAligned ?? null,
          },
        }
      : {}),
    scale: serializeScale(scale),
  };
  return out;
}

/** Build the document that goes up with the PNG.
 *
 * `measured_mm` is computed HERE, at send time, from the current scale -- not
 * cached when the dimension was placed. Calibrating a photo after drawing on
 * it is a normal thing to do, and a cached measurement would go up as the
 * pixel count it was when the user had no scale. */
export function buildDoc(input: DocInput): AnnotationDoc {
  const { scale } = input;
  return {
    version: DOC_VERSION,
    image: IMAGE_NAME,
    source: serializeSource(input.source, scale),
    annotations: input.dimensions.map((dim) => {
      const mm = measureMm(scale, dim.a, dim.b);
      return {
        id: dim.id,
        type: "dimension",
        a: toNormalized(dim.a, scale.pixels),
        b: toNormalized(dim.b, scale.pixels),
        measured_mm: mm === null ? null : roundMm(mm),
        target_mm: dim.targetMm,
        note: dim.note,
        snapped_to: null,
      };
    }),
    caption: input.caption,
  };
}

export function serializeDoc(doc: AnnotationDoc): string {
  return JSON.stringify(doc);
}

function normPoint(value: unknown): NormPoint | null {
  if (!Array.isArray(value) || value.length !== 2) return null;
  const [x, y] = value as unknown[];
  if (typeof x !== "number" || typeof y !== "number") return null;
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return [x, y];
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function parseAnnotation(value: unknown): SerializedDimension | null {
  const raw = value as Record<string, unknown> | null;
  if (!raw || typeof raw !== "object" || raw.type !== "dimension") return null;
  const a = normPoint(raw.a);
  const b = normPoint(raw.b);
  if (!a || !b || typeof raw.id !== "string") return null;
  return {
    id: raw.id,
    type: "dimension",
    a,
    b,
    measured_mm: numberOrNull(raw.measured_mm),
    target_mm: numberOrNull(raw.target_mm),
    note: typeof raw.note === "string" ? raw.note : "",
    snapped_to: null,
  };
}

/** Read a document back, or null if it isn't one we understand.
 *
 * Version handling is deliberately asymmetric. A document with **no** version
 * is treated as version 1 (nothing older than the schema exists, and a missing
 * field is a likelier bug than a time traveller). A document from a **newer**
 * schema is refused outright rather than partially read: the fields we'd
 * recognise might mean something different in it, and half-understanding a
 * measurement is worse than not reading it. Unknown annotation types are
 * dropped rather than failing the document -- adding a kind of mark must not
 * make older readers throw the numbers away. */
export function parseDoc(text: string): AnnotationDoc | null {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return null;
  }
  const body = raw as Record<string, unknown> | null;
  if (!body || typeof body !== "object") return null;
  const version = body.version === undefined ? DOC_VERSION : body.version;
  if (typeof version !== "number" || version > DOC_VERSION || version < 1) return null;

  const annotations = Array.isArray(body.annotations) ? body.annotations : [];
  return {
    version,
    image: typeof body.image === "string" ? body.image : IMAGE_NAME,
    // Passed through as it arrived: `source` is provenance, read by
    // read_device_image (which pulls `id` and does its own defensive get), and
    // validating each field here would only invent a second opinion about it.
    source: (body.source ?? null) as SerializedSource | null,
    annotations: annotations
      .map(parseAnnotation)
      .filter((item): item is SerializedDimension => item !== null),
    caption: typeof body.caption === "string" ? body.caption : "",
  };
}
