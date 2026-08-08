import type { FeatureType, ParseResult, TypeBuffers } from '../types';
import { parseGcode, type SegmentSink } from './gcodeParser';

// Collects segments per (type, layer). Because the gcode is already ordered bottom-to-top,
// appending per layer keeps each type's buffer layer-sorted with no sorting pass.
class Collector implements SegmentSink {
  // type -> (layer index -> flat PRINT coords [x1,y1,z1,x2,y2,z2, ...])
  readonly perType = new Map<FeatureType, number[][]>();
  readonly segCount = new Map<FeatureType, number>();

  // retraction points bucketed by layer (3 print coords per point)
  readonly retract: number[][] = [];
  retractCount = 0;

  hasExtrude = false;
  minX = Infinity; minY = Infinity; minZ = Infinity;
  maxX = -Infinity; maxY = -Infinity; maxZ = -Infinity;

  addSegment(
    type: FeatureType,
    layer: number,
    x1: number, y1: number, z1: number,
    x2: number, y2: number, z2: number,
  ): void {
    let layers = this.perType.get(type);
    if (!layers) {
      layers = [];
      this.perType.set(type, layers);
    }
    let arr = layers[layer];
    if (!arr) {
      arr = [];
      layers[layer] = arr;
    }
    arr.push(x1, y1, z1, x2, y2, z2);
    this.segCount.set(type, (this.segCount.get(type) ?? 0) + 1);

    if (type !== 'travel') {
      this.hasExtrude = true;
      if (x1 < this.minX) this.minX = x1;
      if (x2 < this.minX) this.minX = x2;
      if (x1 > this.maxX) this.maxX = x1;
      if (x2 > this.maxX) this.maxX = x2;
      if (y1 < this.minY) this.minY = y1;
      if (y2 < this.minY) this.minY = y2;
      if (y1 > this.maxY) this.maxY = y1;
      if (y2 > this.maxY) this.maxY = y2;
      if (z1 < this.minZ) this.minZ = z1;
      if (z2 < this.minZ) this.minZ = z2;
      if (z1 > this.maxZ) this.maxZ = z1;
      if (z2 > this.maxZ) this.maxZ = z2;
    }
  }

  addRetraction(layer: number, x: number, y: number, z: number): void {
    let arr = this.retract[layer];
    if (!arr) {
      arr = [];
      this.retract[layer] = arr;
    }
    arr.push(x, y, z);
    this.retractCount++;
  }
}

// Pack one type's per-layer print coords into a single Float32Array, baking the
// print (Z-up) -> THREE world (Y-up) swap: worldX = printX, worldY = printZ, worldZ = -printY.
function pack(layers: number[][], layerCount: number): TypeBuffers {
  const layerStart = new Uint32Array(layerCount + 1);
  let totalCoords = 0;
  for (let L = 0; L < layerCount; L++) {
    const a = layers[L];
    if (a) totalCoords += a.length;
  }
  const positions = new Float32Array(totalCoords);
  let off = 0;
  for (let L = 0; L < layerCount; L++) {
    layerStart[L] = off / 3; // vertex offset
    const a = layers[L];
    if (a) {
      for (let i = 0; i < a.length; i += 3) {
        positions[off] = a[i];          // X
        positions[off + 1] = a[i + 2];  // print Z -> world Y
        positions[off + 2] = -a[i + 1]; // print Y -> world -Z
        off += 3;
      }
    }
  }
  layerStart[layerCount] = off / 3;
  return { positions, layerStart };
}

/** Parse gcode text and build render-ready, layer-sorted buffers per feature type. */
export function parseAndBuild(text: string): ParseResult {
  const c = new Collector();
  const meta = parseGcode(text, c);
  const layerCount = meta.layerCount;

  const byType: Partial<Record<FeatureType, TypeBuffers>> = {};
  const segmentsByType: Partial<Record<FeatureType, number>> = {};
  for (const [type, layers] of c.perType) {
    byType[type] = pack(layers, layerCount);
    segmentsByType[type] = c.segCount.get(type) ?? 0;
  }

  const bounds = c.hasExtrude
    ? {
        min: [c.minX, c.minZ, -c.maxY] as [number, number, number],
        max: [c.maxX, c.maxZ, -c.minY] as [number, number, number],
      }
    : { min: [0, 0, 0] as [number, number, number], max: [1, 1, 1] as [number, number, number] };

  const retractions = { ...pack(c.retract, layerCount), count: c.retractCount };

  return {
    layerCount,
    layerZ: meta.layerZ,
    byType,
    retractions,
    bounds,
    stats: { segmentsByType, unknownFeatureStrings: meta.unknownFeatureStrings },
    warnings: meta.warnings,
  };
}
