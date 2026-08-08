import { describe, it, expect } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { extractGcode } from '../src/parser/container';
import { parseAndBuild } from '../src/parser/buildGeometry';

// Sanity check against the real Bambu sample. Skips automatically if the file is absent.
const samplePath = fileURLToPath(
  new URL('../public/samples/base.gcode.3mf', import.meta.url),
);

describe.skipIf(!existsSync(samplePath))('real Bambu sample', () => {
  it('extracts, parses and buckets the whole print', () => {
    const file = readFileSync(samplePath);
    const ab = file.buffer.slice(file.byteOffset, file.byteOffset + file.byteLength);
    const { gcode, source } = extractGcode('base.gcode.3mf', ab);
    const result = parseAndBuild(gcode);

    const totalSegments = Object.values(result.stats.segmentsByType).reduce(
      (a, b) => a + (b ?? 0),
      0,
    );
    // eslint-disable-next-line no-console
    console.log('source entry :', source);
    // eslint-disable-next-line no-console
    console.log('layerCount   :', result.layerCount);
    // eslint-disable-next-line no-console
    console.log('total segs   :', totalSegments.toLocaleString());
    // eslint-disable-next-line no-console
    console.log('segmentsByType:', result.stats.segmentsByType);
    // eslint-disable-next-line no-console
    console.log('unknown feats :', result.stats.unknownFeatureStrings);
    // eslint-disable-next-line no-console
    console.log('bounds (world):', result.bounds);

    expect(source).toMatch(/\.gcode$/);
    expect(result.layerCount).toBe(1212);
    expect(result.stats.segmentsByType['outer-wall']).toBeGreaterThan(0);
    expect(result.stats.unknownFeatureStrings).toEqual([]);
  });
});
