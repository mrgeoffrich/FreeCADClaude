// SPDX-License-Identifier: LGPL-2.1-or-later
// What a pixel is worth: the derivation, the calibration, the confidence
// rules, and the one rule that has to hold even when everything else fails --
// with no scale, nothing is ever quoted in millimetres.
import { describe, expect, it } from "vitest";

import {
  calibrationScale,
  captureScale,
  dimensionLabel,
  formatMm,
  formatPixels,
  measureLabel,
  measureMm,
  noScale,
  pixelDistance,
  roundMm,
  scaleStatusText,
} from "../src/scale";

const IMAGE = { width: 1280, height: 960 };

/** A published capture's metadata, as send_to_device writes it. */
function meta(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    document: "Bracket",
    view: "front",
    camera: { azimuth: 0, elevation: 0, projection: "orthographic", axis_aligned: true },
    image: { width: 1280, height: 960 },
    scale: {
      mm_per_px: 0.0842,
      confidence: "exact",
      plane: "distances are true in the world X/Z plane; depth (world Y) is not measurable",
    },
    ...overrides,
  };
}

describe("captureScale", () => {
  it("takes the published mm/px and its confidence", () => {
    const scale = captureScale(meta(), IMAGE);
    expect(scale.mmPerPx).toBeCloseTo(0.0842, 9);
    expect(scale.confidence).toBe("exact");
    expect(scale.kind).toBe("capture");
    expect(scale.plane).toContain("X/Z plane");
  });

  it("downgrades to approximate when the camera isn't axis-aligned", () => {
    // The load-bearing downgrade: the NUMBER is kept (Claude has to tell "no
    // measurement" from "a measurement you shouldn't machine to") but it must
    // never read as exact.
    const oblique = captureScale(
      meta({
        camera: { azimuth: 45, elevation: 35, projection: "orthographic", axis_aligned: false },
        scale: { mm_per_px: 0.0842, confidence: "approximate", plane: "foreshortened" },
      }),
      IMAGE,
    );
    expect(oblique.mmPerPx).toBeCloseTo(0.0842, 9);
    expect(oblique.confidence).toBe("approximate");
  });

  it("refuses 'exact' from a payload whose camera says otherwise", () => {
    // Belt and braces on the one claim whose failure mode is a user machining
    // to a foreshortened number: render.py already downgrades, and if a
    // payload ever disagrees with itself, the cautious half wins.
    const lying = captureScale(
      meta({
        camera: { axis_aligned: false },
        scale: { mm_per_px: 0.0842, confidence: "exact" },
      }),
      IMAGE,
    );
    expect(lying.confidence).toBe("approximate");
  });

  it("reads a null scale as no scale at all", () => {
    // The "no orthographic camera" signal. Not an error -- a state.
    const none = captureScale(meta({ scale: null }), IMAGE);
    expect(none.mmPerPx).toBeNull();
    expect(none.confidence).toBe("none");
    expect(none.kind).toBe("none");
  });

  it("survives metadata that is missing, malformed or from another version", () => {
    for (const bad of [undefined, null, {}, { scale: {} }, { scale: { mm_per_px: "0.08" } },
      { scale: { mm_per_px: 0 } }, { scale: { mm_per_px: -1 } }]) {
      expect(captureScale(bad, IMAGE).confidence).toBe("none");
    }
  });

  it("rescales mm/px when the loaded image isn't the published size", () => {
    // Normally identical. If they ever aren't, a pixel of the loaded image is
    // worth proportionally more -- half the width, twice the millimetres.
    const half = captureScale(meta(), { width: 640, height: 480 });
    expect(half.mmPerPx).toBeCloseTo(0.1684, 9);
    expect(half.pixels).toEqual({ width: 640, height: 480 });
  });
});

describe("calibrationScale", () => {
  const pixels = { width: 2000, height: 1500 };

  it("turns two taps and a real length into mm/px", () => {
    // 400px apart, 120mm across -> 0.3 mm/px.
    const scale = calibrationScale({ x: 100, y: 200 }, { x: 500, y: 200 }, 120, pixels);
    expect(scale?.mmPerPx).toBeCloseTo(0.3, 9);
  });

  it("measures the diagonal, not the axis span", () => {
    const scale = calibrationScale({ x: 0, y: 0 }, { x: 30, y: 40 }, 100, pixels);
    expect(scale?.mmPerPx).toBeCloseTo(2, 9); // 50px for 100mm
  });

  it("is never better than approximate, whatever the user types", () => {
    // A photo is right along the calibration line and wrong everywhere else
    // the moment it was shot at an angle, and nothing in the image can tell us
    // it wasn't.
    const scale = calibrationScale({ x: 0, y: 0 }, { x: 1000, y: 0 }, 250, pixels);
    expect(scale?.confidence).toBe("approximate");
    expect(scale?.plane).toContain("flat-on");
  });

  it("refuses a degenerate pair or a nonsense length", () => {
    // Two taps in the same place would divide by ~0 and hand back a scale that
    // reads as astronomically fine rather than as the mis-tap it is.
    expect(calibrationScale({ x: 10, y: 10 }, { x: 10, y: 10.5 }, 120, pixels)).toBeNull();
    expect(calibrationScale({ x: 0, y: 0 }, { x: 400, y: 0 }, 0, pixels)).toBeNull();
    expect(calibrationScale({ x: 0, y: 0 }, { x: 400, y: 0 }, -5, pixels)).toBeNull();
    expect(calibrationScale({ x: 0, y: 0 }, { x: 400, y: 0 }, Number.NaN, pixels)).toBeNull();
  });
});

describe("measurement", () => {
  const exact = captureScale(meta(), IMAGE);

  it("multiplies the pixel distance by mm/px", () => {
    const mm = measureMm(exact, { x: 100, y: 100 }, { x: 400, y: 500 });
    expect(pixelDistance({ x: 100, y: 100 }, { x: 400, y: 500 })).toBeCloseTo(500, 9);
    expect(mm).toBeCloseTo(42.1, 6);
  });

  it("returns null rather than a number when there is no scale", () => {
    expect(measureMm(noScale(IMAGE), { x: 0, y: 0 }, { x: 300, y: 400 })).toBeNull();
  });
});

describe("formatting", () => {
  it("rounds to a precision a tap can actually support", () => {
    // Finer than this is invented precision, and invented precision is what
    // makes a reading look like a specification.
    expect(roundMm(24.3456)).toBe(24.3);
    expect(roundMm(9.87654)).toBe(9.88);
    expect(roundMm(0.004)).toBe(0);
    expect(roundMm(123.456)).toBe(123.5);
  });

  it("drops trailing zeros so a round number reads as one", () => {
    expect(formatMm(30)).toBe("30 mm");
    expect(formatMm(24.3)).toBe("24.3 mm");
    expect(formatPixels(347.6)).toBe("348 px");
  });

  it("shows mm/px at two significant figures, or nothing at all", () => {
    expect(scaleStatusText(captureScale(meta(), IMAGE))).toBe("0.084 mm/px");
    expect(scaleStatusText(noScale(IMAGE))).toBe("");
  });
});

describe("never quotes millimetres without a scale", () => {
  const none = noScale(IMAGE);
  const a = { x: 100, y: 100 };
  const b = { x: 400, y: 500 };

  it("renders a measurement as a pixel count", () => {
    expect(measureLabel(none, a, b)).toBe("500 px");
    expect(measureLabel(none, a, b)).not.toContain("mm");
  });

  it("keeps the user's typed target in mm while the measurement stays pixels", () => {
    // The two are different kinds of fact: "make this 30mm" is an instruction
    // that needs no scale, while "this is 24.3mm" is a claim about the image
    // that we are in no position to make.
    expect(dimensionLabel(none, a, b, 30)).toBe("500 px → 30 mm");
  });

  it("reads as one quantity once there IS a scale", () => {
    const exact = captureScale(meta(), IMAGE);
    expect(dimensionLabel(exact, a, b, null)).toBe("42.1 mm");
    expect(dimensionLabel(exact, a, b, 50)).toBe("42.1 → 50 mm");
  });

  it("ignores a target that isn't a number", () => {
    const exact = captureScale(meta(), IMAGE);
    expect(dimensionLabel(exact, a, b, Number.NaN)).toBe("42.1 mm");
  });
});
