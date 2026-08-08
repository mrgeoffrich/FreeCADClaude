// SPDX-License-Identifier: LGPL-2.1-or-later
import { describe, expect, it } from "vitest";

import { markWelcomeSeen, msgbarCollapsed, setMsgbarCollapsed, welcomeSeen } from "../src/ui";

/** localStorage stand-in -- the tests run in node, with no DOM. */
function fakeStorage(seed: Record<string, string> = {}) {
  const data = new Map(Object.entries(seed));
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
  };
}

describe("first-run help", () => {
  it("shows on a device that has never seen it", () => {
    expect(welcomeSeen(fakeStorage())).toBe(false);
  });

  it("stays dismissed across reloads once it's been read", () => {
    const storage = fakeStorage();
    markWelcomeSeen(storage);
    expect(welcomeSeen(storage)).toBe(true);
  });

  it("ignores a value it didn't write", () => {
    expect(welcomeSeen(fakeStorage({ "fc-welcome-seen": "" }))).toBe(false);
  });
});

describe("message bar", () => {
  it("starts expanded -- a message nobody can see is worse than a bar in the way", () => {
    expect(msgbarCollapsed(fakeStorage())).toBe(false);
  });

  it("stays folded across reloads, which is the point on a small screen", () => {
    const storage = fakeStorage();
    setMsgbarCollapsed(storage, true);
    expect(msgbarCollapsed(storage)).toBe(true);
  });

  it("unfolds again, and remembers that too", () => {
    const storage = fakeStorage({ "fc-msgbar-collapsed": "1" });
    setMsgbarCollapsed(storage, false);
    expect(msgbarCollapsed(storage)).toBe(false);
  });
});
