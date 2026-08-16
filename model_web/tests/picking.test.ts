// SPDX-License-Identifier: LGPL-2.1-or-later
// The pick-resolution logic: a raycast triangle index -> a BRep face ordinal,
// via the worker's brep_faces ranges. Synthetic ranges here, on purpose --
// the real geometry end of the correlation is the promoted Phase 1 test in
// correlation.test.ts; this file pins the pure function's edge behaviour.
import { describe, expect, it } from 'vitest';

import { faceRangeOf, resolveFaceOrdinal } from '../src/picking';
import type { FaceRange } from '../src/worker';

/** Three faces: 2, 8 and 1 triangles wide, with a one-triangle gap between
 * faces 1 and 2 -- nothing in the real data guarantees ranges tile the
 * buffer with no holes, so the resolver must not assume they do. */
const RANGES: FaceRange[] = [
  { first: 0, last: 1 },
  { first: 2, last: 9 },
  { first: 11, last: 11 },
];

describe('resolveFaceOrdinal', () => {
  it('resolves a triangle inside a range to that range\'s ordinal', () => {
    expect(resolveFaceOrdinal(RANGES, 0)).toBe(0);
    expect(resolveFaceOrdinal(RANGES, 5)).toBe(1);
    expect(resolveFaceOrdinal(RANGES, 11)).toBe(2);
  });

  it('treats range bounds as inclusive', () => {
    expect(resolveFaceOrdinal(RANGES, 1)).toBe(0); // range.first
    expect(resolveFaceOrdinal(RANGES, 2)).toBe(1); // range.last of face 0 == first of face 1
    expect(resolveFaceOrdinal(RANGES, 9)).toBe(1); // range.last
  });

  it('returns null for a triangle no range contains', () => {
    expect(resolveFaceOrdinal(RANGES, 10)).toBeNull(); // the gap
    expect(resolveFaceOrdinal(RANGES, 12)).toBeNull(); // past the last range
  });

  it('returns null for an empty or absent range list', () => {
    expect(resolveFaceOrdinal([], 0)).toBeNull();
    expect(resolveFaceOrdinal(RANGES, -1)).toBeNull();
  });

  it('returns the FIRST containing range, preserving face order', () => {
    // Two ranges overlapping would be corrupt data, but if it happens the
    // earlier face wins -- ranges are in face order, so the first hit is the
    // face the triangle belongs to.
    const overlapping: FaceRange[] = [
      { first: 0, last: 5 },
      { first: 3, last: 9 },
    ];
    expect(resolveFaceOrdinal(overlapping, 4)).toBe(0);
  });
});

describe('faceRangeOf', () => {
  it('returns the range of a valid ordinal', () => {
    expect(faceRangeOf(RANGES, 1)).toEqual({ first: 2, last: 9 });
  });

  it('returns null for an out-of-bounds ordinal', () => {
    expect(faceRangeOf(RANGES, -1)).toBeNull();
    expect(faceRangeOf(RANGES, 3)).toBeNull();
    expect(faceRangeOf([], 0)).toBeNull();
  });
});
