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

/** The header name the API's token gate reads. */
export const TOKEN_HEADER = "X-FC-Token";

/** Metadata `send_to_device` published with a capture.
 *
 * Every field is optional on purpose: this crosses a version boundary (a
 * browser tab can outlive a FreeCAD restart), and phase 5 adds `scale` plus
 * `camera.projection`/`camera.axis_aligned` here. Reading it defensively is
 * what makes those additive. */
export interface CaptureMeta {
  readonly document?: string;
  readonly objects?: readonly string[];
  /** Preset name, or null for a custom orbit angle. */
  readonly view?: string | null;
  readonly camera?: { readonly azimuth?: number | null; readonly elevation?: number | null };
  readonly image?: { readonly width?: number; readonly height?: number };
  readonly extents?: string;
  readonly note?: string;
  /** Phase 5: `{mm_per_px, confidence, plane}`. Null means "no scale known". */
  readonly scale?: unknown;
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

/** The annotation document POSTed beside the image.
 *
 * Phase 5 owns this schema (dimensions, normalized coordinates, measured vs
 * target). Phase 4 sends the two things it already has, and `source` is not
 * decoration: it is how `read_device_image` knows which capture the marks were
 * drawn on, and so which camera angle and extents to replay. */
export interface AnnotationDoc {
  readonly caption: string;
  readonly source: { readonly kind: string; readonly id?: string } | null;
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
 * steer where the file lands. */
export function buildUpload(png: Blob, doc: AnnotationDoc): FormData {
  const form = new FormData();
  form.append("image", png, "annotation.png");
  form.append("doc", JSON.stringify(doc));
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
  /** The bytes of a published capture. */
  image(published: Published): Promise<Blob>;
  upload(png: Blob, doc: AnnotationDoc): Promise<UploadResult>;
  /** Subscribe to `published` events. Returns a closer, or null when unpaired. */
  events(onPublished: (published: Published) => void): (() => void) | null;
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
      const response = await fetch("/api/latest", { headers });
      if (!response.ok) throw new Error(await readError(response));
      return parseLatest(await response.json());
    },

    async image(published) {
      const response = await fetch(published.url, { headers });
      if (!response.ok) throw new Error(await readError(response));
      return response.blob();
    },

    async upload(png, doc) {
      // No Content-Type header: the browser has to set it, because only it
      // knows the multipart boundary it generated.
      const response = await fetch("/api/upload", {
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
