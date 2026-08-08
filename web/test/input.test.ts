// SPDX-License-Identifier: LGPL-2.1-or-later
// The pointer policy. This is the one piece of the drawing path that can be
// pinned down without a tablet, and the pen-then-palm sequence is exactly the
// bug you otherwise find by discovering a smear across your drawing.
import { describe, expect, it } from "vitest";

import {
  gestureOf,
  initialInputState,
  observePointer,
  shouldDraw,
  type InputState,
  type PointerLike,
  type PointerRecord,
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

const finger = (id: number, x: number, y: number): PointerRecord => ({ id, type: "touch", x, y });

describe("gestureOf", () => {
  it("reads the midpoint and the spread off two fingers", () => {
    const gesture = gestureOf([finger(1, 100, 200), finger(2, 100, 260)]);
    expect(gesture).toEqual({ mid: { x: 100, y: 230 }, spread: 60 });
  });

  it("is null for one finger, whatever else is down", () => {
    // The whole point of two-finger navigation: a lone contact is a stroke on a
    // penless phone and a resting palm on a tablet. Neither may pan the image.
    expect(gestureOf([finger(1, 10, 10)])).toBeNull();
    expect(gestureOf([])).toBeNull();
  });

  it("ignores a stylus, so a pen plus a palm is not a pinch", () => {
    expect(gestureOf([{ id: 1, type: "pen", x: 0, y: 0 }, finger(2, 50, 50)])).toBeNull();
  });

  it("is null for three fingers, which ends the gesture rather than guessing", () => {
    expect(gestureOf([finger(1, 0, 0), finger(2, 40, 0), finger(3, 80, 0)])).toBeNull();
  });
});
