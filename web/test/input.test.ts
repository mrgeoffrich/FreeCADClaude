// SPDX-License-Identifier: LGPL-2.1-or-later
// The pointer policy. This is the one piece of the drawing path that can be
// pinned down without a tablet, and the pen-then-palm sequence is exactly the
// bug you otherwise find by discovering a smear across your drawing.
import { describe, expect, it } from "vitest";

import {
  initialInputState,
  observePointer,
  shouldDraw,
  type InputState,
  type PointerLike,
} from "../src/input";

const pen: PointerLike = { pointerType: "pen" };
const touch: PointerLike = { pointerType: "touch" };
const mouse: PointerLike = { pointerType: "mouse" };

/** Run a sequence the way the DOM wiring does: observe, then ask. */
function replay(events: PointerLike[]): boolean[] {
  let state = initialInputState();
  return events.map((e) => {
    state = observePointer(state, e);
    return shouldDraw(state, e);
  });
}

describe("shouldDraw", () => {
  it("draws for touch on a device that has never seen a stylus", () => {
    expect(replay([touch, touch])).toEqual([true, true]);
  });

  it("stops drawing for touch once a stylus has been seen", () => {
    // pen down, then a palm lands: the palm must not draw, and the pen must
    // keep drawing.
    expect(replay([pen, touch, pen, touch])).toEqual([true, false, true, false]);
  });

  it("rejects the palm forever, not just for that stroke", () => {
    const [, , afterLift] = replay([pen, pen, touch]);
    expect(afterLift).toBe(false);
  });

  it("counts a stylus HOVER, not just a stylus contact", () => {
    // A pencil announces itself on approach, typically before the palm lands.
    // That event is the one that has to arm the rejection.
    let state = initialInputState();
    state = observePointer(state, pen); // hover move, nothing drawn yet
    expect(shouldDraw(state, touch)).toBe(false);
  });

  it("lets the mouse draw, before and after a stylus", () => {
    expect(replay([mouse, pen, mouse])).toEqual([true, true, true]);
  });

  it("treats an unknown pointer type as a mouse rather than ignoring it", () => {
    expect(replay([{ pointerType: "" }])).toEqual([true]);
  });
});

describe("observePointer", () => {
  it("latches penSeen and never clears it", () => {
    let state: InputState = initialInputState();
    expect(state.penSeen).toBe(false);
    state = observePointer(state, touch);
    expect(state.penSeen).toBe(false);
    state = observePointer(state, pen);
    expect(state.penSeen).toBe(true);
    state = observePointer(state, touch);
    expect(state.penSeen).toBe(true);
  });

  it("returns the same object when nothing changed", () => {
    const state = initialInputState();
    expect(observePointer(state, touch)).toBe(state);
    const armed = observePointer(state, pen);
    expect(observePointer(armed, pen)).toBe(armed);
  });

  it("does not mutate the state it is given", () => {
    const state = initialInputState();
    observePointer(state, pen);
    expect(state.penSeen).toBe(false);
  });
});
