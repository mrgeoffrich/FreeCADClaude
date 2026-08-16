// SPDX-License-Identifier: LGPL-2.1-or-later
// The wire: model_server.py's /api/latest and /api/events stream, parsed
// defensively. Mirrors web/src/api.ts's parsePublished/parseLatest posture --
// a malformed or absent payload reads as "nothing published", which is a
// state the UI already renders, never a thrown error.
//
// No token handling lives here, on purpose: the page was loaded with `?t=`
// and the server set an HttpOnly cookie on that response, so every same-origin
// request -- this module's fetches and the EventSource alike -- authenticates
// itself. The exact same reasoning is stated in gcode_web/src/slicerSettings.ts
// ("The fetches carry no token...") for the same loopback pattern; web/src/
// token.ts belongs to the LAN/QR-pairing flow of a different feature.

/** One object of a published export, as /api/latest reports it. */
export interface ModelObjectEntry {
  /** Server-relative path of the object's .brp bytes, e.g.
   * `/api/mesh/AbC123/Body.brp`. The one field the viewer cannot work
   * without; everything else renders if present. */
  readonly url: string;
  readonly shape_hash: string;
  /** Face ordinal -> {centroid, normal, area}. Not consumed by this phase
   * (face picking is a later phase); carried through the parse so a later
   * phase does not have to loosen this module. */
  readonly faces: Record<string, unknown>;
}

/** What GET /api/latest's `published` field holds, and what a `published`
 * SSE event carries (the same record, whole). */
export interface PublishedModel {
  readonly id: string;
  /** FreeCAD object name -> entry. */
  readonly objects: Record<string, ModelObjectEntry>;
  readonly published_at: number;
}

/** Parse one object entry. Only the url is required; a malformed entry is
 * dropped rather than aborting the publish it arrived in. */
export function parseModelObject(value: unknown): ModelObjectEntry | null {
  if (!value || typeof value !== 'object') return null;
  const record = value as Record<string, unknown>;
  const url = record.url;
  if (typeof url !== 'string' || !url) return null;
  const hash = record.shape_hash;
  const faces = record.faces;
  return {
    url,
    shape_hash: typeof hash === 'string' ? hash : '',
    faces: faces && typeof faces === 'object' ? (faces as Record<string, unknown>) : {},
  };
}

/** Parse a published record (the `published` value of /api/latest, or an SSE
 * `published` event's payload). Null means "nothing loadable", which the UI
 * renders as the empty state.
 *
 * Deliberately strict about only two things -- a non-empty id and a
 * non-empty set of loadable objects -- because those are what the viewer
 * cannot work without. Any object entry that fails to parse is dropped; a
 * publish whose entries all fail is treated as no publish at all. */
export function parsePublishedModel(payload: unknown): PublishedModel | null {
  const record = payload as Record<string, unknown> | null | undefined;
  if (!record || typeof record !== 'object') return null;
  const id = record.id;
  if (typeof id !== 'string' || !id) return null;
  const objectsRaw = record.objects;
  if (!objectsRaw || typeof objectsRaw !== 'object') return null;
  const objects: Record<string, ModelObjectEntry> = {};
  for (const [name, value] of Object.entries(objectsRaw)) {
    const entry = parseModelObject(value);
    if (entry) objects[name] = entry;
  }
  if (Object.keys(objects).length === 0) return null;
  return {
    id,
    objects,
    published_at: typeof record.published_at === 'number' ? record.published_at : 0,
  };
}

/** Parse the whole `{ "published": ... }` envelope of GET /api/latest. */
export function parseLatest(body: unknown): PublishedModel | null {
  const envelope = body as { published?: unknown } | null | undefined;
  if (!envelope || typeof envelope !== 'object') return null;
  return parsePublishedModel(envelope.published);
}

/** Parse the `data` field of an SSE `published` event. The server sends the
 * published record as one JSON string; anything that is not that -- malformed
 * JSON, a non-string, a payload that fails parsePublishedModel -- reads as
 * "nothing published", and the stream stays open for the next event. */
export function parsePublishedEventData(data: unknown): PublishedModel | null {
  if (typeof data !== 'string') return null;
  try {
    return parsePublishedModel(JSON.parse(data));
  } catch {
    return null;
  }
}
