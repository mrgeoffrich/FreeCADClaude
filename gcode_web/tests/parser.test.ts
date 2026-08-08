import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parseGcode } from '../src/parser/gcodeParser';
import { parseAndBuild } from '../src/parser/buildGeometry';
import { flattenArc } from '../src/parser/arc';
import type { FeatureType } from '../src/types';

function fixture(name: string): string {
  return readFileSync(fileURLToPath(new URL(`./fixtures/${name}`, import.meta.url)), 'utf8');
}

// Count emitted segments per feature type via a lightweight sink.
function counts(gcode: string) {
  const byType: Partial<Record<FeatureType, number>> = {};
  const meta = parseGcode(gcode, {
    addSegment: (t) => {
      byType[t] = (byType[t] ?? 0) + 1;
    },
  });
  return { byType, meta };
}

describe('square fixture', () => {
  const result = parseAndBuild(fixture('square.gcode'));

  it('finds one layer at the right Z', () => {
    expect(result.layerCount).toBe(1);
    expect(result.layerZ[0]).toBeCloseTo(0.2);
  });

  it('emits 4 outer-wall edges and 1 travel', () => {
    expect(result.stats.segmentsByType['outer-wall']).toBe(4);
    expect(result.stats.segmentsByType['travel']).toBe(1);
  });

  it('packs 6 floats per segment with a correct layerStart', () => {
    const buf = result.byType['outer-wall']!;
    expect(buf.positions.length).toBe(4 * 6);
    expect(Array.from(buf.layerStart)).toEqual([0, 8]); // 4 segments * 2 vertices
  });

  it('bakes the Z-up -> Y-up axis swap (worldY = printZ, worldZ = -printY)', () => {
    // First outer-wall segment is print (10,10,0) -> (20,10,0).
    const p = result.byType['outer-wall']!.positions;
    expect([p[0], p[1], p[2]]).toEqual([10, 0, -10]);
    expect([p[3], p[4], p[5]]).toEqual([20, 0, -10]);
  });

  it('bounds ignore travel and cover the printed square', () => {
    expect(result.bounds.min[0]).toBeCloseTo(10);
    expect(result.bounds.max[0]).toBeCloseTo(20);
  });
});

describe('square-with-hole fixture', () => {
  const result = parseAndBuild(fixture('square-with-hole.gcode'));

  it('separates outer and inner walls across two layers', () => {
    expect(result.layerCount).toBe(2);
    expect(result.stats.segmentsByType['outer-wall']).toBe(6); // 4 on L0 + 2 on L1
    expect(result.stats.segmentsByType['inner-wall']).toBe(4);
    expect(result.layerZ).toEqual([0.2, 0.4]);
  });

  it('layer-sorts the buffer so each layer is a contiguous draw range', () => {
    const buf = result.byType['outer-wall']!;
    // layerStart[0]=0, layer 0 has 4 segs (8 verts), layer 1 has 2 segs (4 verts)
    expect(Array.from(buf.layerStart)).toEqual([0, 8, 12]);
  });
});

describe('extrusion modes', () => {
  it('M82 (absolute) and M83 (relative) yield the same segments', () => {
    const relative = counts(
      'M83\n; FEATURE: Outer wall\nG1 X10 Y0 E0.5\nG1 X10 Y10 E0.5\nG1 X0 Y10 E0.5\n',
    );
    const absolute = counts(
      'M82\nG92 E0\n; FEATURE: Outer wall\nG1 X10 Y0 E0.5\nG1 X10 Y10 E1.0\nG1 X0 Y10 E1.5\n',
    );
    expect(relative.byType['outer-wall']).toBe(3);
    expect(absolute.byType['outer-wall']).toBe(3);
    expect(absolute.byType['travel']).toBeUndefined();
  });

  it('honours G92 E0 in absolute mode', () => {
    const { byType } = counts(
      'M82\nG92 E0\n; FEATURE: Outer wall\nG1 X10 Y0 E5\nG92 E0\nG1 X10 Y10 E2\n',
    );
    // Without the G92 reset the second move would read as de = 2 - 5 < 0 (a travel).
    expect(byType['outer-wall']).toBe(2);
    expect(byType['travel']).toBeUndefined();
  });
});

describe('feature mapping', () => {
  it('records unrecognised ; FEATURE: strings and buckets them as unknown', () => {
    const { byType, meta } = counts('; FEATURE: Wibble wall\nG1 X0 Y0\nG1 X5 Y0 E1\n');
    expect(byType['unknown']).toBe(1);
    expect(meta.unknownFeatureStrings).toContain('Wibble wall');
  });
});

describe('retractions', () => {
  it('counts a single pure retraction once', () => {
    const r = parseAndBuild(
      'M83\n; FEATURE: Outer wall\nG1 X0 Y0\nG1 X10 Y0 E1\nG1 E-0.8 F1800\n',
    );
    expect(r.retractions.count).toBe(1);
    expect(r.retractions.positions.length).toBe(3); // one point
  });

  it('counts a multi-segment wipe as a single retraction', () => {
    const r = parseAndBuild(
      'M83\n; FEATURE: Outer wall\nG1 X0 Y0\nG1 X10 Y0 E1\n' +
        'G1 X9 Y0 E-0.2\nG1 X8 Y0 E-0.2\nG1 E-0.6\n', // contiguous negative-E run
    );
    expect(r.retractions.count).toBe(1);
  });

  it('counts separate retractions separately', () => {
    const r = parseAndBuild(
      'M83\n; FEATURE: Outer wall\nG1 X0 Y0\n' +
        'G1 X10 Y0 E1\nG1 E-0.8\n' + // retract 1
        'G1 E0.8\nG1 X20 Y0 E1\nG1 E-0.8\n', // deretract resets, extrude, retract 2
    );
    expect(r.retractions.count).toBe(2);
  });

  it('records the retraction at the seam (start of the negative-E run)', () => {
    const r = parseAndBuild(
      'M83\n; FEATURE: Outer wall\nG1 X0 Y0\nG1 X10 Y0 E1\nG1 E-0.8\n',
    );
    // print (10,0,0) -> world (x=10, y=printZ=0, z=-printY=0). `+ 0` normalises -0.
    const p = Array.from(r.retractions.positions.slice(0, 3)).map((v) => v + 0);
    expect(p).toEqual([10, 0, 0]);
  });
});

describe('arc flattening', () => {
  it('subdivides a quarter circle and lands exactly on the end point', () => {
    const pts = flattenArc({ x: 10, y: 0 }, { x: 0, y: 10 }, { x: 0, y: 0 }, false, 0.05);
    expect(pts.length).toBe(8); // ceil((pi/2) / maxStep) with r=10, tol=0.05
    const last = pts[pts.length - 1];
    expect(last.x).toBeCloseTo(0);
    expect(last.y).toBeCloseTo(10);
    for (const p of pts) {
      expect(Math.hypot(p.x, p.y)).toBeCloseTo(10, 1); // stays on the radius
    }
  });

  it('treats a coincident start/end as a full circle', () => {
    const pts = flattenArc({ x: 5, y: 0 }, { x: 5, y: 0 }, { x: 0, y: 0 }, false, 0.05);
    expect(pts.length).toBeGreaterThan(20);
    expect(pts[pts.length - 1].x).toBeCloseTo(5);
    expect(pts[pts.length - 1].y).toBeCloseTo(0);
  });

  it('flattens G2/G3 arcs in a real parse (the circle fixture)', () => {
    const result = parseAndBuild(fixture('arc-circle.gcode'));
    // Two half circles, ~16 chords each at r=10, tol=0.05.
    expect(result.stats.segmentsByType['outer-wall']).toBe(32);
    expect(result.stats.segmentsByType['travel']).toBe(1);
  });
});
