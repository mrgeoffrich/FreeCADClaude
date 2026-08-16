// SPDX-License-Identifier: LGPL-2.1-or-later
// The face-markup document: what the browser sends back when the user
// presses Send.
//
// Deliberately a NEW schema, not a reuse of web/src/doc.ts: that one is flat
// 2D pixel coordinates in image space, this one is 3D face ordinals against
// a published BREP export -- and mixing the two into one shared type is
// exactly the conflation the 2D schema's inert `snapped_to: null` field was
// left there to avoid (the plan and its scope document both say so).
//
// What each mark names:
//
// - `object` is the FreeCAD object name, taken from the mesh's tag -- a name
//   FreeCAD gave the object, never one the browser invents.
// - `face_index` is the 0-based ordinal resolved in picking.ts, i.e. the
//   index into the export's `brep_faces`, which is the same order FreeCAD's
//   Shape.Faces uses. "FaceN" = face_index + 1. The browser never constructs
//   a subelement name itself.
// - `color` is reserved for a future paint-mode UI and is always null in v1.
// - `id` is client-generated ("m1", "m2", ...), purely so the marks list can
//   tell two marks apart; the server never interprets it.
//
// `source.publish_id` is load-bearing: a later phase resolves an uploaded
// mark's face back to a live FreeCAD object using exactly this id to know
// which published set it was drawn against (and, with the recorded shape
// hash, whether that set is still what the document looks like).
//
// `version` is the schema's, and it is written by the browser and read by
// whoever gets the file -- the server stores the body byte for byte and a
// FreeCAD install may be a different vintage than the tab that wrote it, so
// an unknown-future version must be refused rather than half-understood.

/** The schema version written into every document. Bump only for a change a
 * reader cannot ignore. */
export const DOC_VERSION = 1;

/** One marked face. One mark = one face, always: multi-face grouping is a
 * deliberate non-goal of v1, and the schema doesn't foreclose it later. */
export interface FaceMark {
  /** Client-generated, e.g. "m1", "m2", ...; never interpreted by the
   * server. */
  readonly id: string;
  /** The FreeCAD object name (from the mesh's tag). */
  readonly object: string;
  /** The 0-based BRep face ordinal resolved in picking.ts ("FaceN" =
   * face_index + 1). */
  readonly face_index: number;
  /** Reserved for a future paint-mode UI; always null for now. */
  readonly color: string | null;
  readonly note: string;
}

/** The whole markup document, as POSTed to /api/upload. */
export interface ModelMarkupDoc {
  readonly version: number;
  /** The publish this was drawn against. `publish_id` is the "id" of the
   * /api/latest (or SSE `published`) payload that produced the loaded
   * scene. */
  readonly source: {
    readonly publish_id: string;
  };
  readonly marks: readonly FaceMark[];
  readonly caption: string;
}

/** What buildDoc needs to know. A flat shape rather than the app's state, so
 * this module depends on neither api.ts nor the UI. */
export interface ModelMarkupInput {
  readonly publishId: string;
  readonly marks: readonly FaceMark[];
  readonly caption: string;
}

/** Build the document that goes up when the user presses Send. */
export function buildDoc(input: ModelMarkupInput): ModelMarkupDoc {
  return {
    version: DOC_VERSION,
    source: { publish_id: input.publishId },
    marks: input.marks.map((mark) => ({
      id: mark.id,
      object: mark.object,
      face_index: mark.face_index,
      color: null,
      note: mark.note,
    })),
    caption: input.caption,
  };
}

export function serializeDoc(doc: ModelMarkupDoc): string {
  return JSON.stringify(doc);
}

function parseFaceMark(value: unknown): FaceMark | null {
  const raw = value as Record<string, unknown> | null;
  if (!raw || typeof raw !== 'object') return null;
  const id = raw.id;
  const object = raw.object;
  const faceIndex = raw.face_index;
  if (typeof id !== 'string' || !id) return null;
  if (typeof object !== 'string' || !object) return null;
  if (typeof faceIndex !== 'number' || !Number.isInteger(faceIndex) || faceIndex < 0) return null;
  const color = raw.color;
  return {
    id,
    object,
    face_index: faceIndex,
    color: color === null || color === undefined ? null : typeof color === 'string' ? color : null,
    note: typeof raw.note === 'string' ? raw.note : '',
  };
}

/** Read a document back, or null if it isn't one we understand.
 *
 * Version handling mirrors web/src/doc.ts's asymmetry: a document with **no**
 * version is treated as version 1 (nothing older than the schema exists, and
 * a missing field is a likelier bug than a time traveller); a document from
 * a **newer** schema is refused outright rather than partially read. A mark
 * that fails to parse is dropped rather than failing the document -- a
 * future kind of mark must not make an older reader throw the whole thing
 * away -- but the source publish id is required: a markup document without
 * it cannot be resolved back to the geometry it was drawn on. */
export function parseDoc(text: string): ModelMarkupDoc | null {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return null;
  }
  const body = raw as Record<string, unknown> | null;
  if (!body || typeof body !== 'object') return null;
  const version = body.version === undefined ? DOC_VERSION : body.version;
  if (typeof version !== 'number' || version > DOC_VERSION || version < 1) return null;

  const source = body.source as Record<string, unknown> | null | undefined;
  const publishId = source && typeof source === 'object' ? source.publish_id : undefined;
  if (typeof publishId !== 'string' || !publishId) return null;

  const marks = Array.isArray(body.marks) ? body.marks : [];
  return {
    version,
    source: { publish_id: publishId },
    marks: marks
      .map(parseFaceMark)
      .filter((mark): mark is FaceMark => mark !== null),
    caption: typeof body.caption === 'string' ? body.caption : '',
  };
}
