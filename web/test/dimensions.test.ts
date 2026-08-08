// SPDX-License-Identifier: LGPL-2.1-or-later
// Grabbing and snapping dimension endpoints -- under a NON-IDENTITY view
// transform, which is the whole point of the exercise.
//
// Coordinates are stored in image space; a fingertip is a fixed number of CSS
// pixels. Those two facts only agree when the transform is the identity, and
// v1's never is: it's the contain-fit of the image into the canvas. A hit test
// that compared image distances against a constant would make handles easy to
// grab on a small image and impossible on a large one -- something you would
// discover on the first tablet, not in the deferred pinch-zoom.
import { describe, expect, it } from "vitest";

import { identityView, type ViewTransform } from "../src/canvas";
import { HIT_CSS, SNAP_CSS, endpointAt, newDimension, snap } from "../src/dimensions";

/** A 1000x500 image contain-fitted into a small viewport: half size, and
 * offset -- exactly the transform the app runs with. */
const FITTED: ViewTransform = { scale: 0.5, tx: 120, ty: 40 };
/** Zoomed in past 1:1, for the other direction. */
const ZOOMED: ViewTransform = { scale: 4, tx: -300, ty: -80 };

function dims() {
  return [
    newDimension("d1", { x: 100, y: 100 }, { x: 400, y: 100 }),
    newDimension("d2", { x: 600, y: 300 }, { x: 800, y: 300 }),
  ];
}

describe("endpointAt", () => {
  it("grabs an endpoint tapped exactly", () => {
    const items = dims();
    const hit = endpointAt(items, FITTED, { x: 100, y: 100 });
    expect(hit?.dimension.id).toBe("d1");
    expect(hit?.end).toBe("a");
  });

  it("measures the tolerance in SCREEN pixels, not image pixels", () => {
    const items = dims();
    // 40 image px away. At scale 0.5 that is 20 screen px -- inside the 22px
    // grab radius. At scale 4 it is 160 screen px, and nowhere near.
    const near = { x: 140, y: 100 };
    expect(endpointAt(items, FITTED, near)?.end).toBe("a");
    expect(endpointAt(items, ZOOMED, near)).toBeNull();
  });

  it("is unaffected by where the image sits in the viewport", () => {
    // tx/ty cancel between the two points being compared. Asserted because a
    // hit test that mixed image and client coordinates would pass every
    // centred test and fail the moment the image was letterboxed.
    const items = dims();
    const shifted: ViewTransform = { ...FITTED, tx: -9999, ty: 5000 };
    expect(endpointAt(items, shifted, { x: 140, y: 100 })?.end).toBe("a");
  });

  it("returns null when nothing is within reach", () => {
    expect(endpointAt(dims(), FITTED, { x: 250, y: 250 })).toBeNull();
    expect(endpointAt([], FITTED, { x: 100, y: 100 })).toBeNull();
  });

  it("picks the nearest of two endpoints sitting on top of each other", () => {
    // Snapping produces coincident endpoints as a matter of course, and
    // picking whichever was created first would make one unreachable.
    const items = dims();
    items[1]!.a = { x: 404, y: 100 };
    const hit = endpointAt(items, identityView(), { x: 401, y: 100 });
    expect(hit?.dimension.id).toBe("d1");
    expect(hit?.end).toBe("b");
  });

  it("honours an explicit tolerance", () => {
    const items = dims();
    expect(endpointAt(items, identityView(), { x: 100 + HIT_CSS - 1, y: 100 })).not.toBeNull();
    expect(endpointAt(items, identityView(), { x: 100 + HIT_CSS + 1, y: 100 })).toBeNull();
    expect(endpointAt(items, identityView(), { x: 140, y: 100 }, 60)?.end).toBe("a");
  });
});

describe("snap", () => {
  it("pulls a near-miss onto the existing endpoint exactly", () => {
    // This is what makes two dimensions off the same edge share a coordinate
    // instead of disagreeing by the width of the user's pen.
    const items = dims();
    const p = snap(items, identityView(), { x: 100 + SNAP_CSS - 2, y: 102 });
    expect(p).toEqual({ x: 100, y: 100 });
  });

  it("leaves a deliberate placement alone", () => {
    const items = dims();
    const p = { x: 250, y: 260 };
    expect(snap(items, identityView(), p)).toBe(p);
  });

  it("snaps by screen distance, so zooming in lets you place points close together", () => {
    const items = dims();
    const near = { x: 112, y: 100 }; // 12 image px from d1.a
    expect(snap(items, FITTED, near)).toEqual({ x: 100, y: 100 }); // 6 screen px
    expect(snap(items, ZOOMED, near)).toBe(near); // 48 screen px
  });

  it("never snaps the endpoint being dragged to itself", () => {
    const items = dims();
    const ref = { dimension: items[0]!, end: "a" } as const;
    const p = { x: 101, y: 100 };
    expect(snap(items, identityView(), p, SNAP_CSS, ref)).toBe(p);
  });

  it("still snaps a dragged endpoint onto OTHER endpoints", () => {
    // Dragging one end onto another dimension's corner is a deliberate act,
    // and it is how a chain of measurements stays consistent.
    const items = dims();
    const ref = { dimension: items[0]!, end: "a" } as const;
    expect(snap(items, identityView(), { x: 601, y: 301 }, SNAP_CSS, ref)).toEqual({
      x: 600,
      y: 300,
    });
  });
});
