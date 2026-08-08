// SPDX-License-Identifier: LGPL-2.1-or-later
// The pure half of api.ts: how the token is carried, how the multipart body is
// assembled, and how a /api/latest body is read.
//
// The fetches themselves are not tested here -- mocking `fetch` would assert
// that we called our own wrapper, and the thing actually worth checking (that
// the server accepts what we send) is covered on the Python side by
// eval/test_device_server.py, against the real handler.
import { describe, expect, it } from "vitest";

import {
  TOKEN_HEADER,
  authHeaders,
  buildUpload,
  describeCapture,
  eventsUrl,
  parseLatest,
  parsePublished,
} from "../src/api";

describe("authHeaders", () => {
  it("carries the token as X-FC-Token", () => {
    expect(authHeaders("abc123")).toEqual({ [TOKEN_HEADER]: "abc123" });
  });

  it("sends no header at all when unpaired", () => {
    // An empty header value would authenticate as "" and read as a bug in the
    // server's constant-time compare rather than as "we have no token".
    expect(authHeaders(null)).toEqual({});
    expect(authHeaders("")).toEqual({});
  });
});

describe("eventsUrl", () => {
  it("puts the token in the query string -- EventSource can't set headers", () => {
    expect(eventsUrl("abc123")).toBe("/api/events?t=abc123");
  });

  it("escapes a token that would otherwise break the query string", () => {
    // token_urlsafe never produces these, but a hand-edited URL can, and a raw
    // "&" would silently truncate the token to a prefix that still looks valid.
    expect(eventsUrl("a&b=c d")).toBe("/api/events?t=a%26b%3Dc%20d");
  });
});

describe("buildUpload", () => {
  const png = new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" });

  it("names the parts 'image' and 'doc'", async () => {
    const form = buildUpload(png, { caption: "slot too narrow", source: null });
    const image = form.get("image");
    expect(image).toBeInstanceOf(Blob);
    expect(await (image as Blob).arrayBuffer()).toEqual(await png.arrayBuffer());
    expect(JSON.parse(form.get("doc") as string)).toEqual({
      caption: "slot too narrow",
      source: null,
    });
  });

  it("carries the source id, which is how the capture context comes back", () => {
    const form = buildUpload(png, {
      caption: "",
      source: { kind: "freecad_capture", id: "AbC123" },
    });
    expect(JSON.parse(form.get("doc") as string).source).toEqual({
      kind: "freecad_capture",
      id: "AbC123",
    });
  });

  it("sets no Content-Type of its own -- the browser owns the boundary", () => {
    // Asserted as an absence: a hand-set multipart Content-Type without the
    // generated boundary is unparseable, and is the classic way to break this.
    const form = buildUpload(png, { caption: "", source: null });
    expect(form).toBeInstanceOf(FormData);
  });
});

describe("parsePublished", () => {
  const raw = {
    id: "AbC123",
    url: "/api/image/AbC123",
    published_at: 1770000000.5,
    meta: { document: "Bracket", view: "front" },
  };

  it("reads a well-formed record", () => {
    expect(parsePublished(raw)).toEqual({
      id: "AbC123",
      url: "/api/image/AbC123",
      publishedAt: 1770000000.5,
      meta: { document: "Bracket", view: "front" },
    });
  });

  it("treats anything without an id and a url as nothing published", () => {
    expect(parsePublished(null)).toBeNull();
    expect(parsePublished({})).toBeNull();
    expect(parsePublished({ id: "AbC123" })).toBeNull();
    expect(parsePublished({ url: "/api/image/x" })).toBeNull();
    expect(parsePublished({ id: "", url: "/api/image/x" })).toBeNull();
    expect(parsePublished({ id: 7, url: "/api/image/x" })).toBeNull();
  });

  it("survives a record with no metadata", () => {
    // A tab can outlive a FreeCAD restart, so the payload crosses a version
    // boundary: missing fields must read as absent, not throw.
    const out = parsePublished({ id: "x", url: "/api/image/x" });
    expect(out).toEqual({ id: "x", url: "/api/image/x", publishedAt: 0, meta: {} });
  });

  it("unwraps the /api/latest envelope", () => {
    expect(parseLatest({ published: raw })?.id).toBe("AbC123");
    expect(parseLatest({ published: null })).toBeNull();
    expect(parseLatest(null)).toBeNull();
  });
});

describe("describeCapture", () => {
  const record = (meta: Record<string, unknown>) => ({
    id: "x",
    url: "/api/image/x",
    publishedAt: 0,
    meta,
  });

  it("reads as 'document · view'", () => {
    expect(describeCapture(record({ document: "Bracket", view: "front" }))).toBe(
      "Bracket · front view",
    );
  });

  it("says 'custom view' for an orbit angle, which has no preset name", () => {
    expect(describeCapture(record({ document: "Bracket", view: null }))).toBe(
      "Bracket · custom view",
    );
  });

  it("drops a missing document rather than printing 'undefined'", () => {
    expect(describeCapture(record({ view: "top" }))).toBe("top view");
  });
});
