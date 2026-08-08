// SPDX-License-Identifier: LGPL-2.1-or-later
import { describe, expect, it } from "vitest";

import { resolveToken, tokenFromSearch } from "../src/token";

/** sessionStorage stand-in -- the tests run in node, with no DOM. */
function fakeStorage(seed: Record<string, string> = {}) {
  const data = new Map(Object.entries(seed));
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
  };
}

describe("tokenFromSearch", () => {
  it("reads ?t=", () => {
    expect(tokenFromSearch("?t=abc123")).toBe("abc123");
  });

  it("is null when absent or empty", () => {
    expect(tokenFromSearch("")).toBeNull();
    expect(tokenFromSearch("?other=1")).toBeNull();
    expect(tokenFromSearch("?t=")).toBeNull();
  });
});

describe("resolveToken", () => {
  it("stashes the URL token so a later reload still has it", () => {
    const storage = fakeStorage();
    expect(resolveToken("?t=abc123", storage)).toBe("abc123");
    // Second load, no query string (the user hit reload after replaceState).
    expect(resolveToken("", storage)).toBe("abc123");
  });

  it("prefers a fresh URL token over a stale stashed one", () => {
    const storage = fakeStorage({ "fc-device-token": "old" });
    expect(resolveToken("?t=new", storage)).toBe("new");
    expect(resolveToken("", storage)).toBe("new");
  });

  it("is null when unpaired", () => {
    expect(resolveToken("", fakeStorage())).toBeNull();
  });
});
