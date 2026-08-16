// SPDX-License-Identifier: LGPL-2.1-or-later
// Ambient types for occt-import-js@0.0.23. The package ships no .d.ts (its
// package.json has no "types" field), so this is the subset this app uses,
// written against the shape documented in the package's own README and
// verified against the dist/ glue in this repo's VENDORED.md.
declare module 'occt-import-js' {
  export interface OcctFaceRange {
    /** First triangle index of the face (inclusive). */
    first: number;
    /** Last triangle index of the face (inclusive). */
    last: number;
    color: [number, number, number] | null;
  }

  export interface OcctMesh {
    name: string;
    color: [number, number, number] | null;
    /** One entry per source BRep face, in TopExp_Explorer order. Not used by
     * this phase (face picking is a later phase); declared for completeness. */
    brep_faces: OcctFaceRange[];
    attributes: {
      /** Flattened xyz triplets. A typed-array view into the WASM heap, so it
       * must be copied out before the next parse can grow that heap. */
      position: { array: ArrayLike<number> };
      normal?: { array: ArrayLike<number> };
    };
    /** Flattened triangle indices. Same heap-view caveat as position. */
    index: { array: ArrayLike<number> };
  }

  export interface OcctReadResult {
    success: boolean;
    root: { name: string; meshes: number[]; children: unknown[] };
    meshes: OcctMesh[];
  }

  export interface OcctInstance {
    ReadBrepFile(content: Uint8Array, params?: object | null): OcctReadResult;
  }

  export interface OcctModuleOptions {
    /** Where the glue fetches occt-import-js.wasm from. */
    locateFile?: (path: string, prefix: string) => string;
  }

  /** The factory: call it (optionally with locateFile) and await the promise
   * for the initialized OCCT instance. */
  export default function createOcct(options?: OcctModuleOptions): Promise<OcctInstance>;
}
