/// <reference lib="webworker" />
import { buildMesh } from './mesh';
import type { MeshRequest, MeshResponse } from '../types';

const ctx = self as unknown as DedicatedWorkerGlobalScope;

ctx.onmessage = (e: MessageEvent<MeshRequest>) => {
  try {
    const mesh = buildMesh(e.data.buffer);
    const res: MeshResponse = { ok: true, mesh };
    ctx.postMessage(res, [
      mesh.positions.buffer,
      mesh.colors.buffer,
      mesh.indices.buffer,
      mesh.indexLayerStart.buffer,
      mesh.stlPositions.buffer,
      mesh.stlIndices.buffer,
    ]);
  } catch (err) {
    const res: MeshResponse = {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
    ctx.postMessage(res);
  }
};
