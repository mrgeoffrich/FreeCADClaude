import { unzipSync, strFromU8 } from 'fflate';

// Bambu/Orca export `.gcode.3mf` — a ZIP container with the gcode at
// `Metadata/plate_<n>.gcode`. This module returns the raw gcode text whether the
// input is a plain `.gcode` file or a 3MF/ZIP container.

function looksLikeZip(bytes: Uint8Array): boolean {
  // ZIP local-file-header magic: "PK\x03\x04".
  return bytes.length >= 2 && bytes[0] === 0x50 && bytes[1] === 0x4b;
}

/**
 * Extract gcode text from a file buffer.
 * @returns the gcode text and, when relevant, the name of the entry it came from.
 */
export function extractGcode(name: string, buffer: ArrayBuffer): { gcode: string; source: string } {
  const bytes = new Uint8Array(buffer);
  const is3mf = name.toLowerCase().endsWith('.3mf') || name.toLowerCase().endsWith('.zip');

  if (is3mf || looksLikeZip(bytes)) {
    const files = unzipSync(bytes, {
      filter: (f) => /\.gcode$/i.test(f.name),
    });
    const entries = Object.keys(files);
    if (entries.length === 0) {
      throw new Error('No .gcode entry found inside the 3MF/ZIP container.');
    }
    // Prefer Metadata/plate_*.gcode, lowest plate number first.
    entries.sort((a, b) => {
      const pa = plateNumber(a);
      const pb = plateNumber(b);
      return pa - pb;
    });
    const chosen = entries[0];
    return { gcode: strFromU8(files[chosen]), source: chosen };
  }

  return { gcode: strFromU8(bytes), source: name };
}

function plateNumber(path: string): number {
  const m = /plate_(\d+)\.gcode$/i.exec(path);
  return m ? parseInt(m[1], 10) : Number.MAX_SAFE_INTEGER;
}
