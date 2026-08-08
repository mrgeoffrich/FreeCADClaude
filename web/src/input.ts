// SPDX-License-Identifier: LGPL-2.1-or-later
// The pointer policy, and the DOM wiring that applies it.
//
// The policy itself is the single most important detail for a pen device and
// it is five lines: once a stylus has been seen, touch never draws. Palm rest
// then works, and touch is left free for navigation.
//
// Navigation is TWO fingers, always -- never one, on any device. A single
// finger is a drawing stroke on a phone that has no stylus, and a resting palm
// on a tablet that has one; panning off either would move the image while the
// user is trying to mark it up.
//
// Both are expressed as pure functions over plain state so the pen-then-palm
// sequence is a unit test rather than something discovered on a tablet -- see
// test/input.test.ts.

/** The bits of a PointerEvent the policy actually looks at. */
export interface PointerLike {
  readonly pointerType: string;
}

/** What we have learned about the input devices in play. */
export interface InputState {
  /** True once any event has arrived from a stylus -- including a hover move,
   * which is why this is observed separately from drawing: an Apple Pencil or
   * S-Pen announces itself on approach, typically before the palm lands. */
  readonly penSeen: boolean;
}

export function initialInputState(): InputState {
  return { penSeen: false };
}

/** Fold an event into the state. Pure: returns the next state, and the same
 * object when nothing changed. */
export function observePointer(state: InputState, e: PointerLike): InputState {
  if (e.pointerType === "pen" && !state.penSeen) return { penSeen: true };
  return state;
}

/** Should this event contribute ink? Pure -- call `observePointer` first. */
export function shouldDraw(state: InputState, e: PointerLike): boolean {
  // Palm rejection, entire. A touch is only ink on a device that has never
  // shown us a stylus (a plain phone, a finger sketch).
  if (e.pointerType === "touch") return !state.penSeen;
  // Pen always draws; mouse always draws (that is the desktop-debugging path,
  // and a mouse is never a palm). Anything unrecognised is treated as a mouse
  // rather than silently ignored.
  return true;
}

/** One sample of a drawing gesture, in client (viewport) coordinates. The
 * caller maps them into image space through the view transform. */
export interface PointerSample {
  readonly clientX: number;
  readonly clientY: number;
  /** 0..1. Mice report 0.5 while held; pens report real force. */
  readonly pressure: number;
  readonly pointerType: string;
}

/** A pointer that is currently down, in client coordinates. */
export interface PointerRecord {
  readonly id: number;
  readonly type: string;
  readonly x: number;
  readonly y: number;
}

/** A navigation gesture, in client coordinates: where it is and how far apart
 * the fingers are. The view transform needs nothing else. */
export interface GestureSample {
  readonly mid: { readonly x: number; readonly y: number };
  readonly spread: number;
}

/** The gesture the pointers currently down describe, or null.
 *
 * Exactly two touches, and only touches: a pen is drawing, and a third finger
 * is fumbling rather than a new gesture. Returning null there ends the gesture
 * and dropping back to two starts a fresh one, so nothing jumps. */
export function gestureOf(points: readonly PointerRecord[]): GestureSample | null {
  const touches = points.filter((p) => p.type === "touch");
  if (touches.length !== 2) return null;
  const [a, b] = touches as [PointerRecord, PointerRecord];
  return {
    mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 },
    spread: Math.hypot(b.x - a.x, b.y - a.y),
  };
}

export interface GestureHandlers {
  onGestureStart(gesture: GestureSample): void;
  onGestureMove(gesture: GestureSample): void;
  onGestureEnd(): void;
}

export interface DrawHandlers {
  onStart(sample: PointerSample): void;
  onMove(sample: PointerSample): void;
  /** `commit: false` means throw the stroke away -- a cancelled gesture, or a
   * palm stroke overruled the instant the stylus announced itself. */
  onEnd(commit: boolean): void;
  /** Zoom and pan. Optional so the drawing path stands on its own. */
  gesture?: GestureHandlers;
}

function sampleOf(e: PointerEvent): PointerSample {
  return {
    clientX: e.clientX,
    clientY: e.clientY,
    pressure: e.pressure,
    pointerType: e.pointerType,
  };
}

/** Wire the policy to an element. Returns a detach function.
 *
 * Three things here are each a bug if missed:
 * - `touch-action: none` on the element (set in style.css, not here) or the
 *   browser steals the gesture to scroll.
 * - `getCoalescedEvents()`: a 120Hz iPad delivers several samples per frame
 *   and a naive handler keeps only the last one, which visibly corners curves.
 * - pointer capture, so a stroke that leaves the canvas still ends properly.
 */
export function attachDrawing(target: HTMLElement, handlers: DrawHandlers): () => void {
  let state = initialInputState();
  let activeId: number | null = null;
  let activeType = "";
  /** Every pointer currently down, gesture candidate or not. */
  const down = new Map<number, PointerRecord>();
  let gesture: GestureSample | null = null;

  const release = (id: number) => {
    try {
      target.releasePointerCapture(id);
    } catch {
      // Nothing captured it, or the browser refused. Either way it is free.
    }
  };

  const end = (commit: boolean) => {
    if (activeId === null) return;
    release(activeId);
    activeId = null;
    activeType = "";
    handlers.onEnd(commit);
  };

  const endGesture = () => {
    if (!gesture) return;
    gesture = null;
    handlers.gesture?.onGestureEnd();
  };

  /** Promote the pointers down into a gesture, if they now make one. */
  const startGesture = () => {
    if (gesture || !handlers.gesture) return;
    // A pen stroke owns the surface: two contacts arriving under a drawing hand
    // are a palm, not a pinch.
    if (activeId !== null && activeType !== "touch") return;
    const next = gestureOf([...down.values()]);
    if (!next) return;
    // A stroke the first finger started is part of the gesture, not a mark.
    end(false);
    gesture = next;
    handlers.gesture.onGestureStart(next);
  };

  const onDown = (e: PointerEvent) => {
    state = observePointer(state, e);
    down.set(e.pointerId, { id: e.pointerId, type: e.pointerType, x: e.clientX, y: e.clientY });
    startGesture();
    if (gesture) {
      e.preventDefault();
      return;
    }
    // One stroke at a time. A second finger is not a second pen.
    if (activeId !== null) return;
    if (!shouldDraw(state, e)) return;
    e.preventDefault();
    try {
      target.setPointerCapture(e.pointerId);
    } catch {
      // Capture is best-effort; a browser that refuses it still draws.
    }
    activeId = e.pointerId;
    activeType = e.pointerType;
    handlers.onStart(sampleOf(e));
  };

  const onMove = (e: PointerEvent) => {
    const before = state;
    state = observePointer(state, e);
    const record = down.get(e.pointerId);
    if (record) {
      down.set(e.pointerId, { ...record, x: e.clientX, y: e.clientY });
    }

    if (gesture) {
      e.preventDefault();
      const next = gestureOf([...down.values()]);
      if (!next) {
        endGesture();
        return;
      }
      gesture = next;
      handlers.gesture?.onGestureMove(next);
      return;
    }

    // A stylus announced itself mid-stroke and the stroke in progress is a
    // touch: that was the palm. Discard it rather than leaving a smear.
    if (state !== before && activeType === "touch") {
      end(false);
      return;
    }
    if (e.pointerId !== activeId) return;
    e.preventDefault();
    const coalesced = typeof e.getCoalescedEvents === "function" ? e.getCoalescedEvents() : [];
    if (coalesced.length > 0) {
      for (const c of coalesced) handlers.onMove(sampleOf(c));
    } else {
      handlers.onMove(sampleOf(e));
    }
  };

  const lift = (e: PointerEvent, commit: boolean) => {
    down.delete(e.pointerId);
    if (gesture) {
      // Two fingers apart is the gesture; one finger left is the tail of it,
      // not the start of a stroke.
      if (!gestureOf([...down.values()])) endGesture();
      return;
    }
    if (e.pointerId !== activeId) return;
    end(commit);
  };

  const onUp = (e: PointerEvent) => lift(e, true);
  const onCancel = (e: PointerEvent) => lift(e, false);

  target.addEventListener("pointerdown", onDown);
  target.addEventListener("pointermove", onMove);
  target.addEventListener("pointerup", onUp);
  target.addEventListener("pointercancel", onCancel);

  return () => {
    target.removeEventListener("pointerdown", onDown);
    target.removeEventListener("pointermove", onMove);
    target.removeEventListener("pointerup", onUp);
    target.removeEventListener("pointercancel", onCancel);
  };
}
