// SPDX-License-Identifier: LGPL-2.1-or-later
// The wire: the four /api routes, the token that gates them, and the event
// stream that says a new capture arrived.
//
// device_server.py accepts the token three ways (see its `_authorized`), and
// each one exists for a different caller. This module uses two of them:
//
//   X-FC-Token header  every fetch. The token stays out of the URL, so it
//                      can't end up in a history entry or a shared link.
//   ?t= query param    /api/events, and ONLY there. `EventSource` has no way
//                      to set a request header -- that is the whole reason.
//                      It doesn't widen anything: the token arrived in the page
//                      URL to begin with, the response carries
//                      `Referrer-Policy: no-referrer`, and the request is
//                      same-origin.
//
// (The third form, the HttpOnly cookie the page load sets, would in fact reach
// the SSE endpoint too -- a same-origin EventSource sends cookies. It is not
// relied on here because it is invisible from this side: nothing in the page
// can check whether it survived, so a silent 403 would be undiagnosable.)
//
// Paths are absolute rather than relative to `base: "./"`: the app is only ever
// served from this server's root, and an absolute path is the one thing that
// stays right no matter which route the browser thinks it is on.

import { serializeDoc, type AnnotationDoc } from "./doc";

/** The header name the API's token gate reads. */
export const TOKEN_HEADER = "X-FC-Token";

/** Metadata `send_to_device` published with a capture.
 *
 * Every field is optional on purpose: this crosses a version boundary (a
 * browser tab can outlive a FreeCAD restart), so reading it defensively is
 * what lets either side gain a field. The declared types here are a
 * convenience for the app's own code; `scale.captureScale` re-parses the
 * measurement fields from `unknown` rather than trusting them, because a wrong
 * mm/px is the one field whose failure mode is a user machining to it. */
export interface CaptureMeta {
  readonly document?: string;
  readonly objects?: readonly string[];
  /** Preset name, or null for a custom orbit angle. */
  readonly view?: string | null;
  readonly camera?: {
    readonly azimuth?: number | null;
    readonly elevation?: number | null;
    readonly projection?: string | null;
    /** False when the camera doesn't look straight down a world axis, in which
     * case every distance in the image is foreshortened. */
    readonly axis_aligned?: boolean | null;
  };
  readonly image?: { readonly width?: number; readonly height?: number };
  readonly extents?: string;
  readonly note?: string;
  /** Derived from the ortho camera at publish time. Null means "no scale
   * known", which is a state the app renders rather than an error. */
  readonly scale?: {
    readonly mm_per_px?: number | null;
    readonly confidence?: string;
    readonly plane?: string;
  } | null;
  readonly context?: string;
}

/** What `GET /api/latest` reports, and what a `published` SSE event carries. */
export interface Published {
  readonly id: string;
  /** Server-relative path of the bytes, e.g. `/api/image/AbC123`. */
  readonly url: string;
  readonly publishedAt: number;
  readonly meta: CaptureMeta;
}

export function authHeaders(token: string | null): Record<string, string> {
  return token ? { [TOKEN_HEADER]: token } : {};
}

/** The SSE URL, with the token in the query string -- see the note at the top
 * of this file for why this one route is different. */
export function eventsUrl(token: string): string {
  return `/api/events?t=${encodeURIComponent(token)}`;
}

/** The multipart body of an upload: the flattened PNG plus the annotation
 * document as a JSON string.
 *
 * The filename given here is cosmetic and the server ignores it outright -- it
 * generates its own name in one fixed folder, so nothing a client sends can
 * steer where the file lands. It is taken from the document's own `image`
 * field so the two at least agree with each other. */
export function buildUpload(png: Blob, doc: AnnotationDoc): FormData {
  const form = new FormData();
  form.append("image", png, doc.image);
  form.append("doc", serializeDoc(doc));
  return form;
}

/** Parse a `GET /api/latest` body (or an SSE `published` payload) into a
 * `Published`, or null.
 *
 * Pure, and deliberately strict about only two things: an id and a url. Those
 * are the fields the app cannot work without; everything else is metadata it
 * renders if present. Anything malformed reads as "nothing published", which is
 * a state the UI already handles. */
export function parsePublished(payload: unknown): Published | null {
  const record = payload as Record<string, unknown> | null | undefined;
  if (!record || typeof record !== "object") return null;
  const id = record.id;
  const url = record.url;
  if (typeof id !== "string" || !id || typeof url !== "string" || !url) return null;
  const meta = record.meta;
  return {
    id,
    url,
    publishedAt: typeof record.published_at === "number" ? record.published_at : 0,
    meta: meta && typeof meta === "object" ? (meta as CaptureMeta) : {},
  };
}

/** Parse the whole `{ "published": ... }` envelope. */
export function parseLatest(body: unknown): Published | null {
  const envelope = body as { published?: unknown } | null | undefined;
  if (!envelope || typeof envelope !== "object") return null;
  return parsePublished(envelope.published);
}

/** Parse a `GET /api/published` body: `{ "images": [...] }`, oldest first.
 *
 * Anything in the array that doesn't parse as a `Published` (a stray null, a
 * record missing its id) is dropped rather than aborting the whole list --
 * one bad entry shouldn't hide every capture that arrived cleanly. */
export function parseHistory(body: unknown): Published[] {
  const envelope = body as { images?: unknown } | null | undefined;
  const images = envelope && typeof envelope === "object" ? envelope.images : null;
  if (!Array.isArray(images)) return [];
  const out: Published[] = [];
  for (const item of images) {
    const parsed = parsePublished(item);
    if (parsed) out.push(parsed);
  }
  return out;
}

/** A one-line description of a capture, for the status bar and the banner. */
export function describeCapture(published: Published): string {
  const { document, view } = published.meta;
  const parts = [document, view ? `${view} view` : "custom view"].filter(Boolean);
  return parts.join(" · ");
}

export interface UploadResult {
  readonly name: string;
}

export interface DeviceApi {
  /** The currently published capture, or null. */
  latest(): Promise<Published | null>;
  /** Every capture still fetchable this run, oldest first -- what the gallery
   * lists. Unlike `latest`, this survives having missed one or more while the
   * page was closed or backgrounded. */
  history(): Promise<Published[]>;
  /** The bytes of a published capture. */
  image(published: Published): Promise<Blob>;
  upload(png: Blob, doc: AnnotationDoc): Promise<UploadResult>;
  /** Subscribe to `published` events. Returns a closer, or null when unpaired. */
  events(onPublished: (published: Published) => void): (() => void) | null;
}

/** What a rejected `fetch` means here, in the user's terms.
 *
 * `fetch` rejects with a bare `TypeError: Failed to fetch` for every transport
 * failure there is -- the tablet lost wifi, the laptop slept, FreeCAD stopped
 * the server or was quit. None of those are distinguishable from the browser,
 * and all of them have the same two things worth checking, so the message says
 * those rather than repeating the browser's wording. It matters most on the
 * upload: the user has just spent minutes drawing, and "Failed to fetch" over
 * their work does not tell them it is still there. */
const OFFLINE = "no connection to FreeCAD — same wifi, and is the server still on?";

/** `fetch`, with a transport failure turned into that message. */
async function send(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(path, init);
  } catch {
    throw new Error(OFFLINE);
  }
}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { error?: unknown };
    if (typeof body.error === "string") return body.error;
  } catch {
    // A non-JSON error body (403 is plain text) -- the status is the message.
  }
  return `HTTP ${response.status}`;
}

export function deviceApi(token: string | null): DeviceApi {
  const headers = authHeaders(token);

  return {
    async latest() {
      const response = await send("/api/latest", { headers });
      if (!response.ok) throw new Error(await readError(response));
      return parseLatest(await response.json());
    },

    async history() {
      const response = await send("/api/published", { headers });
      if (!response.ok) throw new Error(await readError(response));
      return parseHistory(await response.json());
    },

    async image(published) {
      const response = await send(published.url, { headers });
      if (!response.ok) throw new Error(await readError(response));
      return response.blob();
    },

    async upload(png, doc) {
      // No Content-Type header: the browser has to set it, because only it
      // knows the multipart boundary it generated.
      const response = await send("/api/upload", {
        method: "POST",
        headers,
        body: buildUpload(png, doc),
      });
      if (!response.ok) throw new Error(await readError(response));
      const body = (await response.json()) as { name?: unknown };
      return { name: typeof body.name === "string" ? body.name : "" };
    },

    events(onPublished) {
      if (!token) return null;
      const stream = new EventSource(eventsUrl(token));
      stream.addEventListener("published", (event) => {
        try {
          const published = parsePublished(JSON.parse((event as MessageEvent).data));
          if (published) onPublished(published);
        } catch {
          // A malformed event is not worth breaking the stream over; the next
          // capture will send another one.
        }
      });
      // No onerror handling on purpose: EventSource reconnects by itself, and
      // a FreeCAD restart mints a new token, which is a re-pair rather than
      // something a retry here could fix.
      return () => stream.close();
    },
  };
}
