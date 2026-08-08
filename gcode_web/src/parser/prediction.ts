import { gunzipSync, strFromU8 } from 'fflate';
import type { PredictionBuffers } from '../types';
import { deviationColor, type RGB } from '../deviationColor';

// Parse the Python pipeline's GeoJSON export (optionally gzipped) into a layer-sorted
// segment buffer of predicted-edge boundaries plus per-vertex deviation colours, ready to
// render as an overlay. Coordinates are converted to THREE world space (Z-up -> Y-up) here.

interface Feature {
  geometry: { type: string; coordinates: number[][][] | number[][][][] } | null;
  properties: { layer?: number; z?: number; deviations?: number[] };
}

const devColor = deviationColor;

function isGzip(b: Uint8Array): boolean {
  return b.length > 2 && b[0] === 0x1f && b[1] === 0x8b;
}

export function buildPrediction(buffer: ArrayBuffer): PredictionBuffers {
  let bytes = new Uint8Array(buffer);
  if (isGzip(bytes)) bytes = gunzipSync(bytes);
  const fc = JSON.parse(strFromU8(bytes));
  const features: Feature[] = fc.features ?? [];
  const devScale: number = fc.properties?.devScale ?? 0.2;

  let maxLayer = 0;
  for (const f of features) {
    const L = f.properties?.layer ?? 0;
    if (L > maxLayer) maxLayer = L;
  }
  const layerCount = Math.max(maxLayer + 1, fc.properties?.layerCount ?? 0);

  const perLayerPos: number[][] = [];
  const perLayerCol: number[][] = [];
  let hasDeviation = false;

  const pushSeg = (L: number, p1: number[], p2: number[], z: number, c1: RGB, c2: RGB) => {
    let pos = perLayerPos[L];
    let col = perLayerCol[L];
    if (!pos) {
      pos = [];
      col = [];
      perLayerPos[L] = pos;
      perLayerCol[L] = col;
    }
    pos.push(p1[0], z, -p1[1], p2[0], z, -p2[1]);
    col.push(c1[0], c1[1], c1[2], c2[0], c2[1], c2[2]);
  };

  let count = 0;
  for (const f of features) {
    const g = f.geometry;
    if (!g) continue;
    const L = f.properties?.layer ?? 0;
    const z = f.properties?.z ?? 0;
    const devs = f.properties?.deviations;
    if (devs) hasDeviation = true;
    count++;

    const polys =
      g.type === 'Polygon'
        ? [g.coordinates as number[][][]]
        : g.type === 'MultiPolygon'
          ? (g.coordinates as number[][][][])
          : [];

    for (let pi = 0; pi < polys.length; pi++) {
      const poly = polys[pi];
      for (let ri = 0; ri < poly.length; ri++) {
        const ring = poly[ri];
        // Per-vertex deviations align with the exterior ring of a simple polygon.
        const ringDevs = pi === 0 && ri === 0 ? devs : undefined;
        for (let i = 0; i + 1 < ring.length; i++) {
          const c1 = devColor(ringDevs?.[i], devScale);
          const c2 = devColor(ringDevs?.[i + 1], devScale);
          pushSeg(L, ring[i], ring[i + 1], z, c1, c2);
        }
      }
    }
  }

  const layerStart = new Uint32Array(layerCount + 1);
  let total = 0;
  for (let L = 0; L < layerCount; L++) total += perLayerPos[L]?.length ?? 0;
  const positions = new Float32Array(total);
  const colors = new Float32Array(total);
  let off = 0;
  for (let L = 0; L < layerCount; L++) {
    layerStart[L] = off / 3;
    const pos = perLayerPos[L];
    if (pos) {
      positions.set(pos, off);
      colors.set(perLayerCol[L], off);
      off += pos.length;
    }
  }
  layerStart[layerCount] = off / 3;

  return { positions, colors, layerStart, layerCount, count, devScale, hasDeviation };
}
