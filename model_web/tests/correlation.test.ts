// SPDX-License-Identifier: LGPL-2.1-or-later
// The promoted Phase 1 correlation test: prove that occt-import-js's
// `brep_faces` triangle ranges name the SAME faces, in the SAME order, as
// FreeCAD's own Shape.Faces -- the load-bearing claim the whole pick-to-
// "FaceN" round trip rests on.
//
// Phase 1 proved this by hand with a throwaway spike (spike/face-markup-
// correlation/); this test automates exactly that comparison, CI-runnable
// under Node with no browser and no GPU, and the spike is deleted.
//
// Per shape, the test:
//   1. loads the committed .brp through occt-import-js's own entry point
//      (createOCCT + ReadBrepFile -- the same library call the worker makes,
//      run directly in the test because a Worker is machinery a Node test
//      doesn't need),
//   2. computes one approximate centroid per brep_faces entry by averaging
//      the triangle vertex positions of that entry's inclusive triangle
//      range (the identical computation Phase 1's read_brp.js used),
//   3. loads the fixture's true per-face data (exact CenterOfMass per face,
//      recorded from FreeCAD's Shape.Faces IN ORDER),
//   4. asserts, for every face i: the brep_faces centroid NEAREST to
//      Shape.Faces[i]'s centroid is brep_faces[i]'s -- i.e. the recovered
//      nearest-match assignment is literally the identity permutation.
//
// This is a real correlation check, not a smoke test: a shape with fewer or
// more faces than FreeCAD recorded, ranges that do not cover the mesh's
// triangles exactly, or any non-identity nearest match fails the test with
// both indices and the distance named.
//
// The fixtures were produced by Phase 1's FreeCAD-side script (see
// spike/face-markup-correlation/freecad/export_faces.py, deleted with the
// spike; the JSON records the FreeCAD version that wrote them).
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { beforeAll, describe, expect, it } from 'vitest';
import createOCCT from 'occt-import-js';
import type { OcctInstance } from 'occt-import-js';

const FIXTURES = new URL('./fixtures/', import.meta.url);
const SHAPES = ['box_with_hole', 'filleted_box'] as const;

/** True per-face data as FreeCAD recorded it: `index` is the position in
 * Shape.Faces (0-based), `centroid` the exact CenterOfMass. */
interface FreecadFaceFixture {
  readonly index: number;
  readonly centroid: readonly [number, number, number];
  readonly normal: readonly [number, number, number];
  readonly area: number;
}

interface FreecadFacesFixture {
  readonly shape: string;
  readonly face_count: number;
  readonly faces: readonly FreecadFaceFixture[];
}

function loadFreecadFaces(name: string): FreecadFacesFixture {
  const path = fileURLToPath(new URL(`${name}.freecad.faces.json`, FIXTURES));
  return JSON.parse(readFileSync(path, 'utf8')) as FreecadFacesFixture;
}

function dist(a: readonly number[], b: readonly number[]): number {
  return Math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2);
}

/** Average of a triangle range's vertex positions -- the approximate
 * centroid Phase 1's read_brp.js computed, and what the runtime pick path
 * effectively has to go on. `range` is an inclusive range of TRIANGLE
 * indices; each triangle is 3 consecutive entries of the index buffer, each
 * indexing an xyz triplet of the position buffer. */
function triangleRangeCentroid(
  positions: ArrayLike<number>,
  indices: ArrayLike<number>,
  first: number,
  last: number,
): [number, number, number] {
  const sum = [0, 0, 0];
  for (let t = first; t <= last; t++) {
    const i0 = indices[t * 3] * 3;
    const i1 = indices[t * 3 + 1] * 3;
    const i2 = indices[t * 3 + 2] * 3;
    sum[0] += (positions[i0] + positions[i1] + positions[i2]) / 3;
    sum[1] += (positions[i0 + 1] + positions[i1 + 1] + positions[i2 + 1]) / 3;
    sum[2] += (positions[i0 + 2] + positions[i1 + 2] + positions[i2 + 2]) / 3;
  }
  const n = last - first + 1;
  return [sum[0] / n, sum[1] / n, sum[2] / n];
}

/** The worker's merge, restated for a test: every mesh's position/index
 * arrays concatenated into one buffer and every mesh's ranges joined in
 * mesh order, offset into that merged triangle buffer -- the exact shape
 * the runtime pick path resolves against. */
function mergedFaceRanges(occt: OcctInstance, name: string) {
  const brp = readFileSync(fileURLToPath(new URL(`${name}.brp`, FIXTURES)));
  const result = occt.ReadBrepFile(new Uint8Array(brp), null);
  expect(result.success, `${name}: ReadBrepFile succeeded`).toBe(true);
  let vertexCount = 0;
  let indexCount = 0;
  for (const mesh of result.meshes) {
    vertexCount += mesh.attributes.position.array.length / 3;
    indexCount += mesh.index.array.length;
  }
  const positions = new Float32Array(vertexCount * 3);
  const indices = new Uint32Array(indexCount);
  const ranges: { first: number; last: number }[] = [];
  let vertexOffset = 0;
  let indexOffset = 0;
  for (const mesh of result.meshes) {
    positions.set(mesh.attributes.position.array, vertexOffset * 3);
    const src = mesh.index.array;
    for (let i = 0; i < src.length; i++) {
      indices[indexOffset + i] = src[i] + vertexOffset;
    }
    const triangleOffset = indexOffset / 3;
    for (const range of mesh.brep_faces) {
      ranges.push({ first: range.first + triangleOffset, last: range.last + triangleOffset });
    }
    vertexOffset += mesh.attributes.position.array.length / 3;
    indexOffset += src.length;
  }
  return { ranges, positions, indices };
}

/** One shape's whole correlation check. Returns the mismatches; the caller
 * asserts on them so a failure names every offending face. */
function correlate(name: string, occt: OcctInstance) {
  const fixture = loadFreecadFaces(name);
  const { ranges, positions, indices } = mergedFaceRanges(occt, name);

  // A shape with fewer or more faces than FreeCAD recorded is a hard fail,
  // named before any matching is attempted.
  expect(ranges.length, `${name}: brep_faces face count == Shape.Faces face count`).toBe(
    fixture.face_count,
  );

  // The ranges must tile the mesh's triangle buffer exactly -- the same
  // coverage assertion Phase 1's read_brp.js made.
  const covered = ranges.reduce((sum, range) => sum + (range.last - range.first + 1), 0);
  const totalTriangles = indices.length / 3;
  expect(covered, `${name}: brep_faces ranges cover every triangle exactly`).toBe(totalTriangles);

  const centroids = ranges.map((range) => triangleRangeCentroid(positions, indices, range.first, range.last));

  const mismatches: string[] = [];
  let maxIdentityDistance = 0;
  for (let i = 0; i < fixture.face_count; i++) {
    const freecadCentroid = fixture.faces[i]!.centroid;
    // Nearest brep_faces centroid to THIS FreeCAD face's centroid. Ties go
    // to the lower index, exactly as Python's min() behaved in compare.py.
    let nearest = -1;
    let nearestDistance = Infinity;
    for (let j = 0; j < centroids.length; j++) {
      const d = dist(freecadCentroid, centroids[j]!);
      if (d < nearestDistance) {
        nearestDistance = d;
        nearest = j;
      }
    }
    if (nearest !== i) {
      mismatches.push(
        `face ${i}: Shape.Faces[${i}] centroid ${freecadCentroid} matched ` +
          `brep_faces[${nearest}] centroid ${centroids[nearest]} (d=${nearestDistance.toFixed(6)} mm), ` +
          `not brep_faces[${i}] centroid ${centroids[i]}`,
      );
    } else {
      maxIdentityDistance = Math.max(maxIdentityDistance, nearestDistance);
    }
  }
  return { mismatches, maxIdentityDistance };
}

describe('brep_faces <-> Shape.Faces correlation (promoted from the Phase 1 spike)', () => {
  let occt: OcctInstance;

  beforeAll(async () => {
    // Same factory the worker calls; under Node the glue reads the wasm from
    // its own dist/ directory (the spike's read_brp.js used the identical
    // bare call), so no locateFile is needed.
    occt = await createOCCT();
  }, 120_000);

  for (const name of SHAPES) {
    it(`${name}: brep_faces[i] is the nearest match to Shape.Faces[i], for every i`, () => {
      const { mismatches, maxIdentityDistance } = correlate(name, occt);
      expect(
        mismatches,
        `${name}: every face's nearest brep_faces centroid must be itself` +
          (mismatches.length ? `\n${mismatches.join('\n')}` : ''),
      ).toEqual([]);
      // For the record: planar faces match exactly (0 mm); curved faces match
      // within the triangle-mesh approximation of the centroid (~1 mm on the
      // filleted box's corner patches, comfortably below the nearest other
      // face). The verdict never depends on this number.
      expect(maxIdentityDistance).toBeLessThan(20);
    });
  }
});
