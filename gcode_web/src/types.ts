// Core data model shared between the parser, the worker, and the UI.

/**
 * All feature/line types we render. Order here is the display order in the legend.
 * Values map from Bambu/Orca `; FEATURE:` strings (see featureColors.ts).
 * `travel` = non-extruding moves; `custom` = slicer custom/skirt/brim gcode;
 * `unknown` = a `; FEATURE:` string we don't recognise (rendered magenta so gaps are obvious).
 */
export const FEATURE_TYPES = [
  'outer-wall',
  'inner-wall',
  'overhang-wall',
  'floating-vertical-shell',
  'top-surface',
  'bottom-surface',
  'internal-solid-infill',
  'sparse-infill',
  'gap-infill',
  'bridge',
  'support',
  'support-interface',
  'custom',
  'travel',
  'unknown',
] as const;

export type FeatureType = (typeof FEATURE_TYPES)[number];

/**
 * Per feature type: a flat, layer-sorted vertex buffer plus a CSR-style index of where
 * each layer's vertices begin. `positions` holds 6 floats per line segment
 * (x1,y1,z1, x2,y2,z2) in THREE world space (Y-up; see ParseResult.bounds).
 * `layerStart` has length `layerCount + 1`; layer L occupies vertices
 * [layerStart[L], layerStart[L+1]). Because the buffer is layer-sorted, any contiguous
 * band of layers is a single draw range — see geometry.setDrawRange in ToolpathScene.
 */
export interface TypeBuffers {
  positions: Float32Array;
  layerStart: Uint32Array;
}

/**
 * Point markers (e.g. retractions). Layer-sorted like TypeBuffers but 3 floats per POINT;
 * `layerStart` holds point offsets so the layer slider can draw-range them the same way.
 */
export interface PointBuffers {
  positions: Float32Array;
  layerStart: Uint32Array;
  count: number;
}

export interface ParseResult {
  layerCount: number;
  /** Print Z height (mm) per layer index. Used for the slider readout. */
  layerZ: number[];
  /** Only present feature types appear as keys. */
  byType: Partial<Record<FeatureType, TypeBuffers>>;
  /** Retraction event positions (one per contiguous negative-E run), layer-sorted. */
  retractions: PointBuffers;
  /**
   * Axis-aligned bounds of the extruded geometry, in THREE world space (Y-up).
   * The parser bakes a Z-up (print) -> Y-up (three) swap into the buffers, so these
   * can drive the camera directly. min/max are [x, y, z].
   */
  bounds: { min: [number, number, number]; max: [number, number, number] };
  stats: {
    segmentsByType: Partial<Record<FeatureType, number>>;
    /** Raw `; FEATURE:` strings encountered that we couldn't map. */
    unknownFeatureStrings: string[];
  };
  warnings: string[];
}

/**
 * Predicted physical-edge contours loaded from the Python pipeline's GeoJSON export.
 * Boundary segments are layer-sorted (6 floats/segment) in THREE world space.
 */
export interface PredictionBuffers {
  positions: Float32Array;
  /** Per-vertex RGB (parallel to positions vertices), from the deviation colormap. */
  colors: Float32Array;
  layerStart: Uint32Array;
  layerCount: number;
  count: number; // number of predicted contours
  /** Colour-scale half-range in mm (deviation clamped to ±devScale). */
  devScale: number;
  /** True if any feature carried per-vertex deviations. */
  hasDeviation: boolean;
}

// ---- Worker protocol ----

export interface ParseRequest {
  name: string;
  buffer: ArrayBuffer;
}

export type ParseResponse =
  | { ok: true; result: ParseResult }
  | { ok: false; error: string };

export interface PredictionRequest {
  buffer: ArrayBuffer;
}

export type PredictionResponse =
  | { ok: true; prediction: PredictionBuffers }
  | { ok: false; error: string };

/**
 * The predicted 3D surface mesh (Outer-wall contours extruded + stacked) coloured by true
 * 3D signed distance to the STL surface. Faces are layer-sorted so the slider can draw-range.
 */
export interface MeshBuffers {
  positions: Float32Array; // world coords (Y-up)
  colors: Float32Array; // per-vertex RGB from the deviation colormap
  indices: Uint32Array;
  indexLayerStart: Uint32Array; // length layerCount+1, index offsets per layer
  layerCount: number;
  devScale: number;
  vertexCount: number;
  faceCount: number;
  /** The registered source STL, world coords (empty if not exported). */
  stlPositions: Float32Array;
  stlIndices: Uint32Array;
}

export interface MeshRequest {
  buffer: ArrayBuffer;
}

export type MeshResponse =
  | { ok: true; mesh: MeshBuffers }
  | { ok: false; error: string };
