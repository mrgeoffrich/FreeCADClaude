import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { parseAndBuild } from '../src/parser/buildGeometry';
import type { FeatureType } from '../src/types';

// The FreeCADClaude round trip, guarded end to end at the one seam that can rot
// silently: FreeCAD -> Mesh.export 3MF -> Bambu Studio CLI -> plate_1.gcode ->
// this parser. The fixture is genuine recorded output (Bambu Studio 02.08.01.55,
// two boxes on one plate, 40 layers), gzipped only to keep it small in the repo.
//
// An unmapped `; FEATURE:` string does not fail anything at runtime -- it buckets
// as 'unknown' and draws magenta, which nobody notices for months. So a Studio
// release introducing a new feature name has to fail here instead. When it does,
// the fix is a new entry in FEATURE_ALIASES in src/featureColors.ts.
const fixturePath = fileURLToPath(
  new URL('./fixtures/plate_two_boxes.gcode.gz', import.meta.url),
);

describe('FreeCAD -> Bambu Studio -> viewer round trip', () => {
  const gcode = gunzipSync(readFileSync(fixturePath)).toString('utf8');
  const result = parseAndBuild(gcode);

  it('maps every feature string the slicer emitted', () => {
    expect(result.stats.unknownFeatureStrings).toEqual([]);
    expect(result.stats.segmentsByType['unknown']).toBeUndefined();
  });

  it('builds layers and toolpath from the recorded plate', () => {
    expect(result.layerCount).toBeGreaterThan(0);
    expect(result.layerCount).toBe(40);
    expect(result.stats.segmentsByType['outer-wall']).toBeGreaterThan(0);
    expect(result.warnings).toEqual([]);
  });

  it('covers the feature strings this plate is here to exercise', () => {
    // Every type the fixture's ten `; FEATURE:` strings map to (Custom and Skirt
    // share 'custom'), so a dropped alias fails as a named missing bucket and not
    // only as an unknown string.
    const expected: FeatureType[] = [
      'bottom-surface',
      'bridge',
      'custom',
      'floating-vertical-shell',
      'inner-wall',
      'internal-solid-infill',
      'outer-wall',
      'sparse-infill',
      'top-surface',
    ];
    const missing = expected.filter((t) => !(result.stats.segmentsByType[t] ?? 0));
    expect(missing).toEqual([]);
  });
});
