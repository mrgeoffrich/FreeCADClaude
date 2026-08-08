import type { FeatureType } from '../types';
import { normaliseFeature } from '../featureColors';
import { flattenArc } from './arc';

// Streaming, single-pass G-code state machine for the Bambu/Orca dialect.
// It emits line segments (in PRINT space — X,Y,Z mm, Z-up) to a sink; the axis swap to
// THREE's Y-up world happens later in buildGeometry. Decisions encoded here are confirmed
// against the real sample (BambuStudio 2.07): feature marker is `; FEATURE:`, layers use
// `; CHANGE_LAYER` / `; Z_HEIGHT:`, extrusion is relative (M83), arcs are G2/G3 with I/J.

export interface SegmentSink {
  addSegment(
    type: FeatureType,
    layer: number,
    x1: number,
    y1: number,
    z1: number,
    x2: number,
    y2: number,
    z2: number,
  ): void;
  /** Called once per retraction event, at the position where the retraction begins. */
  addRetraction?(layer: number, x: number, y: number, z: number): void;
}

export interface ParseMeta {
  layerCount: number;
  layerZ: number[];
  unknownFeatureStrings: string[];
  warnings: string[];
}

const EPS_E = 1e-5;

export function parseGcode(text: string, sink: SegmentSink): ParseMeta {
  const warnings: string[] = [];
  const unknown = new Set<string>();
  const layerZ: number[] = [];

  // Before the first `; FEATURE:` marker we're in start/purge gcode — bucket as 'custom'.
  let curType: FeatureType = 'custom';
  let curLayer = -1; // first `; CHANGE_LAYER` bumps this to 0
  let maxLayer = -1;

  let px = 0;
  let py = 0;
  let pz = 0;

  let eAbsolute = false; // M83 (relative) is the Bambu default
  let eModeSeen = false;
  let extrudeBeforeMode = false;
  let lastE = 0;
  let wasRetracting = false; // tracks contiguous negative-E runs so each retraction counts once

  let xyzAbsolute = true; // G90 default
  let inchWarned = false;

  const setFeature = (raw: string) => {
    const name = raw.trim();
    const ft = normaliseFeature(name);
    if (ft) {
      curType = ft;
    } else {
      curType = 'unknown';
      unknown.add(name);
    }
  };

  const handleComment = (payload: string) => {
    const c = payload.trim();
    if (c.startsWith('FEATURE:')) setFeature(c.slice(8));
    else if (c.startsWith('TYPE:')) setFeature(c.slice(5));
    else if (c.startsWith('CHANGE_LAYER') || c.startsWith('LAYER_CHANGE')) curLayer++;
    else if (c.startsWith('Z_HEIGHT:')) recordZ(c.slice(9));
    else if (c.startsWith('Z:')) recordZ(c.slice(2));
  };

  const recordZ = (s: string) => {
    const z = parseFloat(s);
    if (!Number.isNaN(z)) {
      if (curLayer < 0) curLayer = 0;
      layerZ[curLayer] = z;
    }
  };

  const lines = text.split('\n');
  for (let li = 0; li < lines.length; li++) {
    let line = lines[li];
    if (line.length === 0) continue;
    if (line.charCodeAt(line.length - 1) === 13) line = line.slice(0, -1); // trailing \r

    const semi = line.indexOf(';');
    let code: string;
    if (semi >= 0) {
      code = line.slice(0, semi).trim();
      if (code.length === 0) {
        handleComment(line.slice(semi + 1));
        continue;
      }
    } else {
      code = line.trim();
      if (code.length === 0) continue;
    }

    // --- command line ---
    const tokens = code.split(/\s+/);
    const cmd = tokens[0].toUpperCase();

    // Read params (only those we care about). parseFloat handles ".5", "-.5", "E0", etc.
    let X: number | undefined;
    let Y: number | undefined;
    let Z: number | undefined;
    let E: number | undefined;
    let I: number | undefined;
    let J: number | undefined;
    for (let k = 1; k < tokens.length; k++) {
      const tk = tokens[k];
      const v = parseFloat(tk.slice(1));
      if (Number.isNaN(v)) continue;
      switch (tk[0]) {
        case 'X': case 'x': X = v; break;
        case 'Y': case 'y': Y = v; break;
        case 'Z': case 'z': Z = v; break;
        case 'E': case 'e': E = v; break;
        case 'I': case 'i': I = v; break;
        case 'J': case 'j': J = v; break;
      }
    }

    switch (cmd) {
      case 'G0':
      case 'G1': {
        const nx = X !== undefined ? (xyzAbsolute ? X : px + X) : px;
        const ny = Y !== undefined ? (xyzAbsolute ? Y : py + Y) : py;
        const nz = Z !== undefined ? (xyzAbsolute ? Z : pz + Z) : pz;
        const de = extrusionDelta(E);
        detectRetraction(de);
        if (nx !== px || ny !== py || nz !== pz) {
          const ft = de > EPS_E ? curType : 'travel';
          const layer = curLayer < 0 ? 0 : curLayer;
          if (layer > maxLayer) maxLayer = layer;
          sink.addSegment(ft, layer, px, py, pz, nx, ny, nz);
        }
        px = nx; py = ny; pz = nz;
        break;
      }
      case 'G2':
      case 'G3': {
        const ex = X !== undefined ? (xyzAbsolute ? X : px + X) : px;
        const ey = Y !== undefined ? (xyzAbsolute ? Y : py + Y) : py;
        const ez = Z !== undefined ? (xyzAbsolute ? Z : pz + Z) : pz;
        const cx = px + (I ?? 0);
        const cy = py + (J ?? 0);
        const de = extrusionDelta(E);
        detectRetraction(de);
        const ft = de > EPS_E ? curType : 'travel';
        const layer = curLayer < 0 ? 0 : curLayer;
        if (layer > maxLayer) maxLayer = layer;
        const pts = flattenArc({ x: px, y: py }, { x: ex, y: ey }, { x: cx, y: cy }, cmd === 'G2');
        let prevX = px;
        let prevY = py;
        let prevZ = pz;
        for (let p = 0; p < pts.length; p++) {
          sink.addSegment(ft, layer, prevX, prevY, prevZ, pts[p].x, pts[p].y, ez);
          prevX = pts[p].x;
          prevY = pts[p].y;
          prevZ = ez;
        }
        px = ex; py = ey; pz = ez;
        break;
      }
      case 'G90': xyzAbsolute = true; break;
      case 'G91': xyzAbsolute = false; break;
      case 'M82': eAbsolute = true; eModeSeen = true; break;
      case 'M83': eAbsolute = false; eModeSeen = true; break;
      case 'G92':
        if (E !== undefined) lastE = E;
        if (X !== undefined) px = X;
        if (Y !== undefined) py = Y;
        if (Z !== undefined) pz = Z;
        break;
      case 'G21': break; // mm — expected
      case 'G20':
        if (!inchWarned) {
          warnings.push('G20 (inches) encountered — only millimetres (G21) are supported; geometry may be wrong.');
          inchWarned = true;
        }
        break;
      default: break;
    }
  }

  function extrusionDelta(E: number | undefined): number {
    if (E === undefined) return 0;
    if (!eModeSeen) extrudeBeforeMode = true;
    if (eAbsolute) {
      const de = E - lastE;
      lastE = E;
      return de;
    }
    return E;
  }

  // Count one retraction per contiguous negative-E run (a single G1 E- or a multi-segment
  // wipe both count once), recorded at the position where the run starts (the seam).
  function detectRetraction(de: number): void {
    if (de < -EPS_E) {
      if (!wasRetracting) {
        const layer = curLayer < 0 ? 0 : curLayer;
        if (layer > maxLayer) maxLayer = layer;
        sink.addRetraction?.(layer, px, py, pz);
        wasRetracting = true;
      }
    } else if (de > EPS_E) {
      wasRetracting = false;
    }
  }

  if (extrudeBeforeMode) {
    warnings.push('Extrusion seen before M82/M83 — assumed relative E (Bambu default).');
  }

  const layerCount = maxLayer + 1 > 0 ? maxLayer + 1 : 0;
  return { layerCount, layerZ, unknownFeatureStrings: [...unknown], warnings };
}
