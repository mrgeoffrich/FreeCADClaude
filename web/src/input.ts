// SPDX-License-Identifier: LGPL-2.1-or-later
// The pointer policy, and the DOM wiring that applies it.
//
// The policy itself is the single most important detail for a pen device and
// it is five lines: once a stylus has been seen, touch never draws. Palm rest
// then works, and touch is left unambiguously free for navigation gestures
// later (pinch-zoom is deferred, but it can only ever be added cleanly if
// touch was never overloaded onto drawing).
//
// It is expressed as pure functions over a plain state object so the
// pen-then-palm sequence is a unit test rather than something discovered on a
// tablet -- see test/input.test.ts.

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

export interface DrawHandlers {
  onStart(sample: PointerSample): void;
  onMove(sample: PointerSample): void;
  /** `commit: false` means throw the stroke away -- a cancelled gesture, or a
   * palm stroke overruled the instant the stylus announced itself. */
  onEnd(commit: boolean): void;
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

  const end = (commit: boolean) => {
    if (activeId === null) return;
    activeId = null;
    activeType = "";
    handlers.onEnd(commit);
  };

  const onDown = (e: PointerEvent) => {
    state = observePointer(state, e);
    // One stroke at a time. A second finger is not a second pen; it is the
    // start of a gesture we don't handle yet.
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

  const onUp = (e: PointerEvent) => {
    if (e.pointerId !== activeId) return;
    end(true);
  };

  const onCancel = (e: PointerEvent) => {
    if (e.pointerId !== activeId) return;
    end(false);
  };

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
