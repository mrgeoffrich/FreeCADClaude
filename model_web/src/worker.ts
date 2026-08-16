// SPDX-License-Identifier: LGPL-2.1-or-later
// The occt-import-js parse worker. Loading the BREP bytes and tessellating
// them is real CPU work -- BRepMesh_IncrementalMesh in WASM -- and it must
// not contend with orbiting the loaded model on the UI thread, so it runs
// here, in a plain Vite module worker (new Worker(new URL("./worker.ts",
// import.meta.url), { type: "module" })), per the library's own recommended
// pattern.
//
// The library's WASM build has no -pthread flag, so no COOP/COEP headers are
// needed; the worker is about responsiveness, not shared memory.
//
// Protocol, deliberately plain: the main thread sends one {kind: "parse"}
// message per object with the .brp bytes as an ArrayBuffer (transferred
// zero-copy), and gets back either a {kind: "parsed"} message carrying fresh
// position/normal/index typed arrays (transferred zero-copy) plus the
// per-face triangle ranges, or a {kind: "error"} message. Enough to build one
// THREE.BufferGeometry per object and to resolve a raycast triangle back to
// the BRep face ordinal it tessellated from (Phase 4's picking).
import createOCCT from 'occt-import-js';
import wasmUrl from 'occt-import-js/dist/occt-import-js.wasm?url';
import type { OcctInstance } from 'occt-import-js';

export interface ParseRequest {
  kind: 'parse';
  /** Opaque correlation id, echoed back so the main thread can match a reply
   * to the object it asked for even if requests interleave. */
  id: number;
  /** The raw .brp bytes. Transferred, not copied. */
  buffer: ArrayBuffer;
}

/** One source BRep face's share of the mesh, exactly as occt-import-js
 * reports it: an INCLUSIVE range of triangle indices into the index buffer.
 * ``{first: 0, last: 1}`` covers triangles 0 and 1 -- index-buffer entries
 * 0..5. The array's own order is the faces' order (the library walks
 * TopExp_Explorer(TopAbs_FACE), the same traversal FreeCAD's Shape.Faces
 * uses), and that order is what makes a picked triangle resolvable to a
 * "FaceN" ordinal by construction. */
export interface FaceRange {
  first: number;
  last: number;
}

export interface ParsedPayload {
  kind: 'parsed';
  id: number;
  /** Flattened xyz triplets. */
  positions: Float32Array;
  /** Flattened xyz triplets; null when the source had none (caller computes
   * vertex normals then). */
  normals: Float32Array | null;
  /** Flattened triangle indices. */
  indices: Uint32Array;
  /** One entry per source BRep face, in the order ReadBrepFile returned
   * them. Ranges index into THIS payload's merged ``indices`` buffer, so
   * ranges from every mesh after the first are offset by the triangles
   * merged before them. Every entry survives the merge -- even a range
   * covering no triangles -- because dropping one would shift every ordinal
   * after it. */
  brepFaces: FaceRange[];
}

export interface ParseErrorPayload {
  kind: 'error';
  id: number;
  message: string;
}

export type WorkerReply = ParsedPayload | ParseErrorPayload;

// The WASM kernel loads once per tab, not once per tool call -- a ~7 MB
// download is the whole reason the plan's invariant 4 exists. One promise,
// shared by every parse.
let occtPromise: Promise<OcctInstance> | null = null;

function loadOcct(): Promise<OcctInstance> {
  occtPromise ??= createOCCT({ locateFile: () => wasmUrl });
  return occtPromise;
}

/** Copy one mesh's arrays out of the WASM heap into fresh typed arrays. The
 * library returns views into its own heap, which a later parse can grow and
 * invalidate, so everything posted back must be a copy. */
function copyMeshArrays(
  mesh: { attributes: { position: { array: ArrayLike<number> }; normal?: { array: ArrayLike<number> } }; index: { array: ArrayLike<number> } },
) {
  const positions = new Float32Array(mesh.attributes.position.array);
  const indices = new Uint32Array(mesh.index.array);
  const normals =
    mesh.attributes.normal && mesh.attributes.normal.array.length === positions.length
      ? new Float32Array(mesh.attributes.normal.array)
      : null;
  return { positions, indices, normals };
}

/** Merge every mesh the parse returned into one geometry's arrays.
 *
 * ReadBrepFile can return several meshes -- a BREP export of a multi-solid
 * object, or an object made of more than one shape -- and this phase wants
 * one mesh per FreeCAD OBJECT, not one per source face or solid. Merging is
 * a plain concat with an index offset; the faces' triangle ranges survive it
 * because picking (Phase 4) resolves a raycast triangle against exactly this
 * merged buffer. Each mesh's ranges are offset by the triangles merged
 * before it, in order, one entry per source face -- see FaceRange. */
function mergeMeshes(
  meshes: ReturnType<OcctInstance['ReadBrepFile']>['meshes'],
): { positions: Float32Array; normals: Float32Array | null; indices: Uint32Array; brepFaces: FaceRange[] } | null {
  if (meshes.length === 0) return null;
  let vertexCount = 0;
  let indexCount = 0;
  let allHaveNormals = true;
  for (const mesh of meshes) {
    vertexCount += mesh.attributes.position.array.length / 3;
    indexCount += mesh.index.array.length;
    if (!mesh.attributes.normal || mesh.attributes.normal.array.length === 0) allHaveNormals = false;
  }
  const positions = new Float32Array(vertexCount * 3);
  const indices = new Uint32Array(indexCount);
  const normals = allHaveNormals ? new Float32Array(vertexCount * 3) : null;
  const brepFaces: FaceRange[] = [];

  let vertexOffset = 0;
  let indexOffset = 0;
  for (const mesh of meshes) {
    const { positions: srcPositions, indices: srcIndices, normals: srcNormals } =
      copyMeshArrays(mesh);
    positions.set(srcPositions, vertexOffset * 3);
    if (normals && srcNormals) normals.set(srcNormals, vertexOffset * 3);
    for (let i = 0; i < srcIndices.length; i++) {
      indices[indexOffset + i] = srcIndices[i] + vertexOffset;
    }
    const triangleOffset = indexOffset / 3;
    for (const range of mesh.brep_faces ?? []) {
      brepFaces.push({ first: range.first + triangleOffset, last: range.last + triangleOffset });
    }
    vertexOffset += srcPositions.length / 3;
    indexOffset += srcIndices.length;
  }
  return { positions, normals, indices, brepFaces };
}

self.onmessage = async (event: MessageEvent<ParseRequest>) => {
  const request = event.data;
  if (!request || request.kind !== 'parse') return;
  const { id, buffer } = request;
  try {
    const occt = await loadOcct();
    const result = occt.ReadBrepFile(new Uint8Array(buffer), null);
    if (!result.success) {
      postError(id, 'occt-import-js could not read the BREP file');
      return;
    }
    const merged = mergeMeshes(result.meshes);
    if (!merged || merged.positions.length === 0) {
      postError(id, 'the BREP file tessellated to no geometry');
      return;
    }
    const payload: ParsedPayload = {
      kind: 'parsed',
      id,
      positions: merged.positions,
      normals: merged.normals,
      indices: merged.indices,
      brepFaces: merged.brepFaces,
    };
    const transfer: Transferable[] = [merged.positions.buffer, merged.indices.buffer];
    if (merged.normals) transfer.push(merged.normals.buffer);
    self.postMessage(payload, transfer);
  } catch (error) {
    postError(id, error instanceof Error ? error.message : String(error));
  }
};

function postError(id: number, message: string): void {
  const payload: ParseErrorPayload = { kind: 'error', id, message };
  self.postMessage(payload);
}
