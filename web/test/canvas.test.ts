// SPDX-License-Identifier: LGPL-2.1-or-later
// The pure geometry behind the canvas: the downscale the flatten applies
// before a PNG goes over the wire, and the view transform every stored
// coordinate passes through.
//
// The rendering itself is deliberately not tested -- a jsdom canvas harness
// would assert that draw calls happened, not that the ink looks right, and the
// only place gesture feel can be judged is on the two real devices.
import { describe, expect, it } from "vitest";

import {
  MAX_FLAT_EDGE,
  MAX_ZOOM,
  applyGesture,
  clampView,
  fitView,
  fitWithin,
  identityView,
  panBy,
  toImage,
  toScreen,
  zoomAt,
} from "../src/canvas";

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

// The pinch. A 1000x500 image in an 800x800 viewport: fit scale 0.8, letterboxed
// with 200px of empty above and below.
const IMAGE = { width: 1000, height: 500 };
const VIEWPORT = { width: 800, height: 800 };
const FIT = fitView(IMAGE, VIEWPORT);

describe("zoomAt", () => {
  it("holds the anchor point still", () => {
    const anchor = { x: 610, y: 250 };
    const under = toImage(FIT, anchor);
    const after = toImage(zoomAt(FIT, anchor, 3), anchor);
    expect(after.x).toBeCloseTo(under.x, 9);
    expect(after.y).toBeCloseTo(under.y, 9);
  });

  it("multiplies the scale", () => {
    expect(zoomAt(FIT, { x: 400, y: 400 }, 2.5).scale).toBeCloseTo(FIT.scale * 2.5, 9);
  });

  it("refuses a factor that would produce NaN coordinates", () => {
    expect(zoomAt(FIT, { x: 400, y: 400 }, 0)).toBe(FIT);
    expect(zoomAt(FIT, { x: 400, y: 400 }, Number.NaN)).toBe(FIT);
  });
});

describe("applyGesture", () => {
  it("keeps the image under the midpoint while the fingers spread", () => {
    const from = { mid: { x: 300, y: 400 }, spread: 100 };
    const to = { mid: { x: 500, y: 350 }, spread: 200 };
    const under = toImage(FIT, from.mid);
    const next = applyGesture(FIT, from, to);
    expect(next.scale).toBeCloseTo(FIT.scale * 2, 9);
    // The point the fingers grabbed has followed them to where they now are.
    expect(toScreen(next, under).x).toBeCloseTo(to.mid.x, 9);
    expect(toScreen(next, under).y).toBeCloseTo(to.mid.y, 9);
  });

  it("is a pure pan when the spread does not change", () => {
    const next = applyGesture(FIT, { mid: { x: 300, y: 400 }, spread: 120 }, { mid: { x: 340, y: 380 }, spread: 120 });
    expect(next.scale).toBeCloseTo(FIT.scale, 9);
    expect(next.tx).toBeCloseTo(FIT.tx + 40, 9);
    expect(next.ty).toBeCloseTo(FIT.ty - 20, 9);
  });

  it("treats a degenerate spread as no zoom rather than dividing by it", () => {
    const next = applyGesture(FIT, { mid: { x: 10, y: 10 }, spread: 0 }, { mid: { x: 10, y: 10 }, spread: 90 });
    expect(next.scale).toBeCloseTo(FIT.scale, 9);
  });
});

describe("clampView", () => {
  it("refuses to zoom out past the contain-fit", () => {
    const out = clampView({ scale: FIT.scale / 4, tx: 0, ty: 0 }, IMAGE, VIEWPORT);
    expect(out).toEqual(FIT);
  });

  it("caps the zoom, holding the viewport centre", () => {
    const centre = { x: 400, y: 400 };
    const wild = zoomAt(FIT, centre, 500);
    const out = clampView(wild, IMAGE, VIEWPORT);
    expect(out.scale).toBeCloseTo(FIT.scale * MAX_ZOOM, 9);
    // What was in the middle of the screen is still in the middle of it.
    expect(toImage(out, centre).x).toBeCloseTo(toImage(FIT, centre).x, 6);
    expect(toImage(out, centre).y).toBeCloseTo(toImage(FIT, centre).y, 6);
  });

  it("keeps the image edges outside the viewport once it is bigger than it", () => {
    const zoomed = zoomAt(FIT, { x: 400, y: 400 }, 4);
    const shoved = clampView(panBy(zoomed, 5000, 5000), IMAGE, VIEWPORT);
    // Dragged hard to the bottom right: the image's top-left corner lands on
    // the viewport's, and no further.
    expect(shoved.tx).toBeCloseTo(0, 9);
    expect(shoved.ty).toBeCloseTo(0, 9);

    const other = clampView(panBy(zoomed, -5000, -5000), IMAGE, VIEWPORT);
    expect(other.tx).toBeCloseTo(VIEWPORT.width - IMAGE.width * zoomed.scale, 9);
    expect(other.ty).toBeCloseTo(VIEWPORT.height - IMAGE.height * zoomed.scale, 9);
  });

  it("centres an axis the image does not fill, however far it is panned", () => {
    // At 2x fit the image is 1600x800 -- wider than the viewport, exactly as
    // tall. The vertical pan must stay centred rather than drifting.
    const out = clampView(panBy({ scale: FIT.scale * 2, tx: -100, ty: 0 }, 0, 300), IMAGE, VIEWPORT);
    expect(out.ty).toBeCloseTo(0, 9);
    expect(out.tx).toBeCloseTo(-100, 9);
  });

  it("falls back to the fit on a view that has gone non-finite", () => {
    expect(clampView({ scale: 0, tx: 0, ty: 0 }, IMAGE, VIEWPORT)).toEqual(FIT);
    expect(clampView({ scale: 1, tx: Number.NaN, ty: 0 }, IMAGE, VIEWPORT)).toEqual(FIT);
  });

  it("gives up on a degenerate image or viewport", () => {
    expect(clampView(FIT, { width: 0, height: 0 }, VIEWPORT)).toEqual(identityView());
  });
});
