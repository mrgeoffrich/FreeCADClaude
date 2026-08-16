// SPDX-License-Identifier: LGPL-2.1-or-later
// Picking: how a click on the tessellated BRep becomes a face ordinal.
//
// The chain, all client-side:
//
//   raycaster.intersectObject(mesh)  ->  intersection.faceIndex
//     faceIndex is the TRIANGLE ordinal in the geometry's index buffer
//     (three.js numbers faces per triangle, not per vertex -- a face with
//     index t covers index-buffer entries [3t, 3t+1, 3t+2]).
//   resolveFaceOrdinal(brepFaces, faceIndex)
//     -> the ordinal of the brep_faces entry whose inclusive triangle range
//        contains that triangle, or null when no range contains it.
//
// That ordinal IS the 0-based index Phase 2's model_export.py used
// ("FaceN" = ordinal + 1), so no further translation is needed: a mark is
// the raw (object, face_index) pair, and the server's read-back phase turns
// it into a FreeCAD subelement name.
//
// The worker's brep_faces ranges live in the SAME index-buffer space the
// raycast faceIndex numbers (the worker offsets every mesh's ranges into its
// merged buffer; App.tsx keeps the geometry's triangle order when it makes
// it non-indexed), so the search below is a plain containment check over
// inclusive ranges. Linear search is fine: this is a click handler, not a
// hot loop, and a typical object has dozens of faces, not thousands.
import type { FaceRange } from './worker';

/** Resolve a raycast triangle index to the 0-based BRep face ordinal whose
 * range contains it, or null when no range does (a triangle the library
 * left outside every face range, or a corrupt/empty range list).
 *
 * The search is deliberately order-preserving: the ranges are in face
 * order, so the FIRST range containing the triangle is the face the
 * triangle tessellated from. */
export function resolveFaceOrdinal(brepFaces: readonly FaceRange[], triangleIndex: number): number | null {
  for (let i = 0; i < brepFaces.length; i++) {
    const range = brepFaces[i];
    if (triangleIndex >= range.first && triangleIndex <= range.last) return i;
  }
  return null;
}

/** The inclusive triangle range of one face ordinal, or null when the
 * ordinal is out of bounds. What a hover/mark highlight paints: the vertices
 * of triangles [range.first, range.last] (each triangle is 3 consecutive
 * vertices of a non-indexed geometry). */
export function faceRangeOf(brepFaces: readonly FaceRange[], faceIndex: number): FaceRange | null {
  if (faceIndex < 0 || faceIndex >= brepFaces.length) return null;
  return brepFaces[faceIndex];
}
