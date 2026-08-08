// SPDX-License-Identifier: LGPL-2.1-or-later
// The annotation document: the round trip, the normalized coordinates that
// make it survive a downscale, and the measured-vs-target split that is the
// whole reason it carries numbers at all.
import { describe, expect, it } from "vitest";

import { newDimension } from "../src/dimensions";
import {
  DOC_VERSION,
  buildDoc,
  fromNormalized,
  parseDoc,
  serializeDoc,
  toNormalized,
} from "../src/doc";
import { captureScale, noScale, type Scale } from "../src/scale";

const IMAGE = { width: 1000, height: 500 };

const CAPTURE_META = {
  document: "Bracket",
  view: "front",
  camera: { azimuth: 0, elevation: 0, projection: "orthographic", axis_aligned: true },
  image: { width: 1000, height: 500 },
  scale: {
    mm_per_px: 0.1,
    confidence: "exact",
    plane: "distances are true in the world X/Z plane; depth (world Y) is not measurable",
  },
};

const exact: Scale = captureScale(CAPTURE_META, IMAGE);

const SOURCE = {
  kind: "freecad_capture",
  id: "AbC123",
  document: "Bracket",
  view: "front",
  projection: "orthographic",
  axisAligned: true,
};

describe("normalized coordinates", () => {
  it("converts image pixels to 0..1 of each axis", () => {
    expect(toNormalized({ x: 250, y: 400 }, IMAGE)).toEqual([0.25, 0.8]);
  });

  it("round-trips back to the same pixel", () => {
    const back = fromNormalized(toNormalized({ x: 313, y: 217 }, IMAGE), IMAGE);
    expect(back.x).toBeCloseTo(313, 2);
    expect(back.y).toBeCloseTo(217, 2);
  });

  it("means the same place after the flatten's downscale", () => {
    // The point of normalizing: the PNG that goes over the wire is capped at a
    // 1568px long edge, so the image Claude sees is often not the one the user
    // drew on. 0.25 has to be a quarter of the way across both.
    const n = toNormalized({ x: 250, y: 400 }, IMAGE);
    const scaled = fromNormalized(n, { width: 2000, height: 1000 });
    expect(scaled).toEqual({ x: 500, y: 800 });
  });

  it("clamps a point that somehow landed off the image", () => {
    expect(toNormalized({ x: -40, y: 900 }, IMAGE)).toEqual([0, 1]);
  });

  it("degrades to the origin rather than NaN on a degenerate image", () => {
    expect(toNormalized({ x: 10, y: 10 }, { width: 0, height: 0 })).toEqual([0, 0]);
  });
});

describe("buildDoc", () => {
  const dimension = () => {
    const dim = newDimension("d1", { x: 100, y: 250 }, { x: 400, y: 250 });
    dim.targetMm = 45;
    dim.note = "widen the slot";
    return dim;
  };

  it("writes the shape from the design doc", () => {
    const doc = buildDoc({
      caption: "slot too narrow",
      source: SOURCE,
      scale: exact,
      dimensions: [dimension()],
    });
    expect(doc.version).toBe(DOC_VERSION);
    expect(doc.caption).toBe("slot too narrow");
    expect(doc.source).toEqual({
      kind: "freecad_capture",
      id: "AbC123",
      document: "Bracket",
      camera: { projection: "orthographic", view: "front", axis_aligned: true },
      scale: {
        mm_per_px: 0.1,
        confidence: "exact",
        plane: CAPTURE_META.scale.plane,
      },
    });
    expect(doc.annotations).toEqual([
      {
        id: "d1",
        type: "dimension",
        a: [0.1, 0.5],
        b: [0.4, 0.5],
        measured_mm: 30,
        target_mm: 45,
        note: "widen the slot",
        snapped_to: null,
      },
    ]);
  });

  it("keeps measured_mm and target_mm as separate facts", () => {
    // "This is 30mm" is information; "make it 45mm" is an instruction. A
    // document that collapsed them would lose the delta -- and lose the
    // difference between a dimension the user was reading off and one they
    // were asking for.
    const untargeted = newDimension("d1", { x: 100, y: 250 }, { x: 400, y: 250 });
    const doc = buildDoc({ caption: "", source: null, scale: exact, dimensions: [untargeted] });
    expect(doc.annotations[0]?.measured_mm).toBe(30);
    expect(doc.annotations[0]?.target_mm).toBeNull();
  });

  it("measures at SEND time, so calibrating after drawing still counts", () => {
    // The user placing a dimension before setting the scale is normal; a
    // measurement cached at placement would go up as the pixel count it was.
    const dim = newDimension("d1", { x: 100, y: 250 }, { x: 400, y: 250 });
    const before = buildDoc({ caption: "", source: null, scale: noScale(IMAGE), dimensions: [dim] });
    expect(before.annotations[0]?.measured_mm).toBeNull();
    const after = buildDoc({ caption: "", source: null, scale: exact, dimensions: [dim] });
    expect(after.annotations[0]?.measured_mm).toBe(30);
  });

  it("never writes a millimetre measurement without a scale", () => {
    const doc = buildDoc({
      caption: "",
      source: { kind: "device_photo" },
      scale: noScale(IMAGE),
      dimensions: [dimension()],
    });
    expect(doc.annotations[0]?.measured_mm).toBeNull();
    // The scale slot is present and explicitly null: "you cannot read a
    // millimetre off this", rather than an absent field that reads as an
    // oversight. The user's own target survives -- it is their instruction.
    expect(doc.source).toEqual({ kind: "device_photo", scale: null });
    expect(doc.annotations[0]?.target_mm).toBe(45);
  });

  it("reserves snapped_to and always writes null", () => {
    // Geometry ray-picking is a later phase; the field exists now so that
    // phase is additive rather than a schema change.
    const doc = buildDoc({ caption: "", source: null, scale: exact, dimensions: [dimension()] });
    expect(doc.annotations[0]?.snapped_to).toBeNull();
  });

  it("serializes no freehand ink -- the picture already carries it", () => {
    const doc = buildDoc({ caption: "", source: null, scale: exact, dimensions: [dimension()] });
    expect(serializeDoc(doc)).not.toContain("stroke");
    expect(Object.keys(doc).sort()).toEqual(["annotations", "caption", "image", "source", "version"]);
  });
});

describe("round trip", () => {
  it("survives serialize -> parse unchanged", () => {
    const dim = newDimension("d1", { x: 100, y: 250 }, { x: 400, y: 250 });
    dim.targetMm = 45;
    dim.note = "widen the slot";
    const doc = buildDoc({ caption: "hi", source: SOURCE, scale: exact, dimensions: [dim] });
    expect(parseDoc(serializeDoc(doc))).toEqual(doc);
  });

  it("reads a document with no version as version 1", () => {
    // Nothing older than the schema exists, so a missing field is a likelier
    // bug than a time traveller.
    const parsed = parseDoc(JSON.stringify({ image: "annotation.png", annotations: [], caption: "" }));
    expect(parsed?.version).toBe(1);
  });

  it("refuses a document from a newer schema outright", () => {
    // Half-understanding a measurement is worse than not reading it: the
    // fields we recognise might mean something different in that version.
    expect(parseDoc(JSON.stringify({ version: DOC_VERSION + 1, annotations: [] }))).toBeNull();
    expect(parseDoc(JSON.stringify({ version: 0, annotations: [] }))).toBeNull();
    expect(parseDoc(JSON.stringify({ version: "1", annotations: [] }))).toBeNull();
  });

  it("drops an annotation kind it doesn't know, keeping the ones it does", () => {
    // Adding a kind of mark must not make an older reader throw the numbers
    // away.
    const parsed = parseDoc(
      JSON.stringify({
        version: 1,
        annotations: [
          { id: "x1", type: "callout", a: [0, 0] },
          { id: "d1", type: "dimension", a: [0.1, 0.5], b: [0.4, 0.5], measured_mm: 30, target_mm: null },
        ],
      }),
    );
    expect(parsed?.annotations.map((a) => a.id)).toEqual(["d1"]);
  });

  it("reads anything unparseable as 'not a document'", () => {
    expect(parseDoc("")).toBeNull();
    expect(parseDoc("{")).toBeNull();
    expect(parseDoc("null")).toBeNull();
    expect(parseDoc('"a string"')).toBeNull();
  });
});
