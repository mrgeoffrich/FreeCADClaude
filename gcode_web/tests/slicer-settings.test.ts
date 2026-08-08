// SPDX-License-Identifier: LGPL-2.1-or-later
// Local patch (FreeCADClaude) -- see VENDORED.md.
//
// The settings drawer's choosing rules, which is all of it that is not
// rendering. The one worth pinning is presetFor: changing the nozzle re-derives
// all three presets, so a process the new machine cannot use has to be replaced
// rather than left showing. Leaving it would put a name in the form that the
// server refuses on save, and the user would be reading a setting they do not
// have.

import { describe, expect, it } from 'vitest';
import {
  machineFor,
  nozzleFor,
  presetFor,
  printerFor,
  type SlicerOptions,
} from '../src/slicerSettings';

const P2S = 'Bambu Lab P2S';
const P1S = 'Bambu Lab P1S';
const M04 = 'Bambu Lab P2S 0.4 nozzle';
const M02 = 'Bambu Lab P2S 0.2 nozzle';

function options(overrides: Partial<SlicerOptions> = {}): SlicerOptions {
  return {
    printers: [
      {
        model: P2S,
        vendor: 'BBL',
        nozzles: ['0.2', '0.4'],
        machines: { '0.2': M02, '0.4': M04 },
        materials: [],
      },
      { model: P1S, vendor: 'BBL', nozzles: ['0.4'], machines: {}, materials: [] },
    ],
    selected: { machine: M04, process: null, filament: null },
    machine: M04,
    processes: [
      { name: '0.20mm Standard @BBL P2S', default: true },
      { name: '0.16mm Optimal @BBL P2S', default: false },
    ],
    filaments: [{ name: 'Bambu PLA Basic @BBL P2S', default: true }],
    nozzle_default: '0.4',
    ...overrides,
  };
}

describe('which printer the drawer shows', () => {
  it('prefers the stored one', () => {
    expect(printerFor(options(), { printer: P1S })).toBe(P1S);
  });

  it('falls back to whichever printer owns the filtered machine', () => {
    expect(printerFor(options({ machine: M02 }), {})).toBe(P2S);
  });

  it('ignores a stored printer that is no longer installed', () => {
    expect(printerFor(options(), { printer: 'Prusa MK4' })).toBe(P2S);
  });

  it('has an answer with no printers at all', () => {
    expect(printerFor(options({ printers: [], machine: null }), {})).toBe('');
  });
});

describe('which nozzle it shows', () => {
  it('prefers the stored one', () => {
    expect(nozzleFor(options(), P2S, { nozzle: '0.2' })).toBe('0.2');
  });

  it('drops a stored nozzle the chosen printer does not have', () => {
    // Selecting the other printer must not leave 0.2 showing against a printer
    // that only comes in 0.4.
    expect(nozzleFor(options(), P1S, { nozzle: '0.2' })).toBe('0.4');
  });

  it('otherwise follows the machine the server filtered for', () => {
    expect(nozzleFor(options({ machine: M02 }), P2S, {})).toBe('0.2');
  });

  it('defaults to the vendor base nozzle when nothing else says', () => {
    expect(nozzleFor(options({ machine: null }), P2S, {})).toBe('0.4');
  });
});

describe('the machine preset for a printer and nozzle', () => {
  it('is read off the map the server sent', () => {
    expect(machineFor(options(), P2S, '0.2')).toBe(M02);
  });

  it('is empty when none is installed for that combination', () => {
    expect(machineFor(options(), P1S, '0.4')).toBe('');
    expect(machineFor(options(), P2S, '0.8')).toBe('');
  });
});

describe('what a re-filter does to a chosen preset', () => {
  const offered = options().processes;

  it('keeps a choice that survived the filter', () => {
    expect(presetFor(offered, '0.16mm Optimal @BBL P2S')).toBe('0.16mm Optimal @BBL P2S');
  });

  it("replaces one the new machine cannot use with that machine's default", () => {
    expect(presetFor(offered, '0.10mm Standard @BBL P2S 0.2 nozzle')).toBe(
      '0.20mm Standard @BBL P2S',
    );
  });

  it('falls back to the first when nothing is marked default', () => {
    const undeclared = offered.map((entry) => ({ ...entry, default: false }));
    expect(presetFor(undeclared, 'gone')).toBe('0.20mm Standard @BBL P2S');
  });

  it('is empty when the machine can use nothing at all', () => {
    expect(presetFor([], 'anything')).toBe('');
  });
});
