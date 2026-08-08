import { gunzipSync, strFromU8 } from 'fflate';
import type { MeshBuffers } from '../types';
import { deviationColor } from '../deviationColor';

// Parse the Python pipeline's predicted-mesh export (gzipped JSON) into typed arrays for an
// indexed BufferGeometry. Positions are converted to THREE world space (print Z-up -> Y-up)
// and per-vertex colours come from the deviation colormap.

interface MeshJson {
  meta: { layerCount: number; devScale: number; indexLayerStart: number[] };
  positions: number[]; // flat x,y,z in print coords
  indices: number[];
  deviations: number[]; // per vertex (mm)
  stl?: { positions: number[]; indices: number[] }; // registered STL, print coords
}

// print coords (Z up) -> THREE world (Y up): (x, z, -y)
function swapToWorld(flat: number[]): Float32Array {
  const out = new Float32Array(flat.length);
  for (let i = 0; i < flat.length; i += 3) {
    out[i] = flat[i];
    out[i + 1] = flat[i + 2];
    out[i + 2] = -flat[i + 1];
  }
  return out;
}

function isGzip(b: Uint8Array): boolean {
  return b.length > 2 && b[0] === 0x1f && b[1] === 0x8b;
}

export function buildMesh(buffer: ArrayBuffer): MeshBuffers {
  let bytes = new Uint8Array(buffer);
  if (isGzip(bytes)) bytes = gunzipSync(bytes);
  const j: MeshJson = JSON.parse(strFromU8(bytes));

  const pos = j.positions;
  const dev = j.deviations;
  const devScale = j.meta.devScale ?? 0.2;
  const vertexCount = pos.length / 3;

  const positions = new Float32Array(pos.length);
  const colors = new Float32Array(pos.length);
  for (let i = 0; i < vertexCount; i++) {
    const x = pos[i * 3];
    const y = pos[i * 3 + 1];
    const z = pos[i * 3 + 2];
    positions[i * 3] = x; // world X
    positions[i * 3 + 1] = z; // print Z -> world Y
    positions[i * 3 + 2] = -y; // print Y -> world -Z
    const c = deviationColor(dev[i], devScale);
    colors[i * 3] = c[0];
    colors[i * 3 + 1] = c[1];
    colors[i * 3 + 2] = c[2];
  }

  const stlPositions = j.stl ? swapToWorld(j.stl.positions) : new Float32Array(0);
  const stlIndices = j.stl ? Uint32Array.from(j.stl.indices) : new Uint32Array(0);

  return {
    positions,
    colors,
    indices: Uint32Array.from(j.indices),
    indexLayerStart: Uint32Array.from(j.meta.indexLayerStart),
    layerCount: j.meta.layerCount,
    devScale,
    vertexCount,
    faceCount: j.indices.length / 3,
    stlPositions,
    stlIndices,
  };
}
