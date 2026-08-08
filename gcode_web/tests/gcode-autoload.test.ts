// SPDX-License-Identifier: LGPL-2.1-or-later
// Local patch (FreeCADClaude) -- see VENDORED.md.
//
// The two pure halves of the autoload seam. `view_gcode` opens the page as
// `?t=<token>&gcode=<id>`, so the id has to survive being read out of a query
// string that also carries the token, and the loaded file has to end up under
// the name the server states rather than under the id, which has no extension
// and would send a `.gcode.3mf` to the wrong branch.
//
// The fetch and the render need a browser and are a manual check.

import { describe, expect, it } from 'vitest';
import { gcodeIdFromSearch, isMeshFile, isPredictionFile, statedName } from '../src/hooks/useGcodeFile';

describe('the gcode id in a page URL', () => {
  it('is read out from beside the token', () => {
    expect(gcodeIdFromSearch('?t=abc123&gcode=Kx3Jf9aQ')).toBe('Kx3Jf9aQ');
  });

  it('is read whichever order the two arrive in', () => {
    expect(gcodeIdFromSearch('?gcode=Kx3Jf9aQ&t=abc123')).toBe('Kx3Jf9aQ');
  });

  it('is percent-decoded, since page_url quotes it', () => {
    expect(gcodeIdFromSearch('?gcode=a%2Fb')).toBe('a/b');
  });

  it('is null when the page was opened for the settings panel alone', () => {
    expect(gcodeIdFromSearch('?t=abc123')).toBeNull();
    expect(gcodeIdFromSearch('')).toBeNull();
  });

  it('is null rather than empty when the parameter carries nothing', () => {
    // An empty id would be fetched as /api/gcode/ and 404, which reads to the
    // user as a broken viewer rather than as an empty one.
    expect(gcodeIdFromSearch('?gcode=')).toBeNull();
    expect(gcodeIdFromSearch('?gcode=%20%20')).toBeNull();
  });
});

describe('the filename the server states', () => {
  it('comes out of the Content-Disposition header', () => {
    expect(statedName('inline; filename="plate_1.gcode"')).toBe('plate_1.gcode');
  });

  it('keeps a container name pointing at the container branch', () => {
    const name = statedName('inline; filename="plate_1.gcode.3mf"');
    expect(name).toBe('plate_1.gcode.3mf');
    expect(isMeshFile(name)).toBe(false);
    expect(isPredictionFile(name)).toBe(false);
  });

  it('is empty when there is no header, so the caller falls back', () => {
    expect(statedName(null)).toBe('');
    expect(statedName('inline')).toBe('');
  });
});
