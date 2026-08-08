// SPDX-License-Identifier: LGPL-2.1-or-later
// The pure geometry behind the canvas: the downscale the flatten applies
// before a PNG goes over the wire, and the view transform every stored
// coordinate passes through.
//
// The rendering itself is deliberately not tested -- a jsdom canvas harness
// would assert that draw calls happened, not that the ink looks right, and the
// only place gesture feel can be judged is on the two real devices.
import { describe, expect, it } from "vitest";

import { MAX_FLAT_EDGE, fitView, fitWithin, identityView, toImage, toScreen } from "../src/canvas";

describe("fitWithin", () => {
  it("leaves an image under the cap untouched", () => {
    expect(fitWithin({ width: 800, height: 600 }, 1568)).toEqual({ width: 800, height: 600 });
  });

  it("leaves an image exactly on the cap untouched", () => {
    expect(fitWithin({ width: 1568, height: 900 }, 1568)).toEqual({ width: 1568, height: 900 });
  });

  it("scales a landscape image to the cap on its long edge", () => {
    // 4032x3024 -- a 12MP phone photo, the case this exists for.
    expect(fitWithin({ width: 4032, height: 3024 }, 1568)).toEqual({ width: 1568, height: 1176 });
  });

  it("scales a portrait image the same way", () => {
    expect(fitWithin({ width: 3024, height: 4032 }, 1568)).toEqual({ width: 1176, height: 1568 });
  });

  it("rounds the short edge and keeps the long edge exact", () => {
    // 3000x1997 -> 1568 x 1043.836... : the long edge must land on the cap
    // exactly, and the short edge must be an integer (a fractional canvas
    // size is silently truncated by the browser).
    const out = fitWithin({ width: 3000, height: 1997 }, 1568);
    expect(out).toEqual({ width: 1568, height: 1044 });
    expect(Number.isInteger(out.height)).toBe(true);
  });

  it("never rounds a very thin image away to nothing", () => {
    expect(fitWithin({ width: 20000, height: 5 }, 1568)).toEqual({ width: 1568, height: 1 });
  });

  it("preserves the aspect ratio to within a rounded pixel", () => {
    const source = { width: 4032, height: 3024 };
    const out = fitWithin(source, MAX_FLAT_EDGE);
    expect(out.width / out.height).toBeCloseTo(source.width / source.height, 3);
  });

  it("handles a square image and a degenerate one", () => {
    expect(fitWithin({ width: 2000, height: 2000 }, 1568)).toEqual({ width: 1568, height: 1568 });
    expect(fitWithin({ width: 0, height: 0 }, 1568)).toEqual({ width: 0, height: 0 });
  });
});

describe("view transform", () => {
  it("round-trips a point through screen space", () => {
    const view = { scale: 0.37, tx: 12, ty: -8 };
    const back = toImage(view, toScreen(view, { x: 123, y: 456 }));
    expect(back.x).toBeCloseTo(123, 9);
    expect(back.y).toBeCloseTo(456, 9);
  });

  it("centres a contain-fit and reports the scale", () => {
    // A 1000x500 image in an 800x800 viewport fits on width and is letterboxed.
    const view = fitView({ width: 1000, height: 500 }, { width: 800, height: 800 });
    expect(view.scale).toBeCloseTo(0.8, 9);
    expect(view.tx).toBeCloseTo(0, 9);
    expect(view.ty).toBeCloseTo(200, 9);
    expect(toScreen(view, { x: 500, y: 250 })).toEqual({ x: 400, y: 400 });
  });

  it("falls back to identity on a degenerate image or viewport", () => {
    expect(fitView({ width: 0, height: 0 }, { width: 800, height: 600 })).toEqual(identityView());
    expect(fitView({ width: 100, height: 100 }, { width: 0, height: 0 })).toEqual(identityView());
  });
});
