// SPDX-License-Identifier: LGPL-2.1-or-later
// The eraser's hit test. Whether a rub catches a stroke is the whole of how
// the tool feels, and it is pure geometry over image-space points -- so it is
// pinned down here rather than found by missing an obvious mark on a tablet.
import { describe, expect, it } from "vitest";

import { addPoint, newStroke, strokeHitsPoint, type Stroke } from "../src/strokes";

/** A stroke through the given points, at a 4px width. */
function strokeThrough(points: Array<[number, number]>, size = 4): Stroke {
  const stroke = newStroke(size, false);
  for (const [x, y] of points) addPoint(stroke, { x, y, pressure: 0.5 });
  return stroke;
}

describe("strokeHitsPoint", () => {
  it("catches a point on the stroke", () => {
    const stroke = strokeThrough([
      [0, 0],
      [100, 0],
    ]);
    expect(strokeHitsPoint(stroke, { x: 50, y: 0 }, 10)).toBe(true);
  });

  it("misses a point beyond the tolerance", () => {
    const stroke = strokeThrough([
      [0, 0],
      [100, 0],
    ]);
    expect(strokeHitsPoint(stroke, { x: 50, y: 40 }, 10)).toBe(false);
  });

  it("catches between two samples, not just at them", () => {
    // A fast stroke samples sparsely. Testing the samples alone would let the
    // eraser pass straight through the middle of a long straight line.
    const stroke = strokeThrough([
      [0, 0],
      [400, 0],
    ]);
    expect(strokeHitsPoint(stroke, { x: 200, y: 5 }, 6)).toBe(true);
  });

  it("does not reach past the ends of a segment", () => {
    const stroke = strokeThrough([
      [0, 0],
      [100, 0],
    ]);
    expect(strokeHitsPoint(stroke, { x: -40, y: 0 }, 10)).toBe(false);
  });

  it("adds half the stroke's width, so a fat stroke is caught by its edge", () => {
    const thin = strokeThrough([[50, 50]], 4);
    const fat = strokeThrough([[50, 50]], 40);
    const at = { x: 50, y: 62 };
    expect(strokeHitsPoint(thin, at, 5)).toBe(false);
    expect(strokeHitsPoint(fat, at, 5)).toBe(true);
  });

  it("handles a single-point stroke -- a dot is a mark too", () => {
    const dot = strokeThrough([[10, 10]]);
    expect(strokeHitsPoint(dot, { x: 12, y: 12 }, 6)).toBe(true);
    expect(strokeHitsPoint(dot, { x: 90, y: 90 }, 6)).toBe(false);
  });

  it("never hits an empty stroke", () => {
    expect(strokeHitsPoint(strokeThrough([]), { x: 0, y: 0 }, 1000)).toBe(false);
  });
});
