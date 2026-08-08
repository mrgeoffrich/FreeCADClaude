// SPDX-License-Identifier: LGPL-2.1-or-later
// Local patch (FreeCADClaude) -- see VENDORED.md.
//
// The slicer settings panel: which printer, nozzle, process and filament the
// addon slices with, and whether it orients and arranges. A drawer over the
// viewer rather than a route of its own, so there is one page and one bundle.
//
// It renders whether or not a G-code file is loaded, because configuring the
// printer is what comes BEFORE the first slice -- view_gcode opens this page
// with nothing to show for exactly that.
//
// Changing the nozzle re-fetches the options rather than filtering what is
// already here: process and filament compatibility is a field inside each
// preset file (compatible_printers), so only the server can say what a
// different machine may use.

import { useCallback, useEffect, useState } from 'react';
import {
  fetchOptions,
  fetchSettings,
  machineFor,
  nozzleFor,
  presetFor,
  printerFor,
  printerNamed,
  saveSettings,
  type SlicerOptions,
  type SlicerSettings,
} from '../slicerSettings';

interface SettingsDrawerProps {
  onClose: () => void;
}

/** The form's own state: what is shown, before it is stored. */
interface Draft {
  printer: string;
  nozzle: string;
  process: string;
  filament: string;
  orient: boolean;
  arrange: boolean;
  deviation: string;
}

const DEFAULT_DEVIATION = '0.1';

function draftFrom(options: SlicerOptions, settings: SlicerSettings): Draft {
  const printer = printerFor(options, settings);
  const nozzle = nozzleFor(options, printer, settings);
  return {
    printer,
    nozzle,
    process: presetFor(options.processes, settings.process),
    filament: presetFor(options.filaments, settings.filament),
    orient: settings.orient !== false,
    arrange: settings.arrange !== false,
    deviation: settings.deviation === undefined ? DEFAULT_DEVIATION : String(settings.deviation),
  };
}

export function SettingsDrawer({ onClose }: SettingsDrawerProps) {
  const [options, setOptions] = useState<SlicerOptions | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [path, setPath] = useState('');
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState('');

  // The drawer is mounted only while it is open (see App.tsx), so this runs
  // once per opening and every field starts from what is stored rather than
  // from what was on screen last time. It is also why the read is not done on
  // page load: it costs a few thousand small preset-file reads on a full vendor
  // tree, and most page loads are here to look at a toolpath.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const stored = await fetchSettings();
        const found = await fetchOptions(stored.settings.machine);
        if (cancelled) return;
        setPath(stored.path);
        setOptions(found);
        setDraft(draftFrom(found, stored.settings));
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  /** Re-filter the preset lists for a newly chosen printer or nozzle. */
  const refilter = useCallback(
    async (printer: string, nozzle: string) => {
      if (!options) return;
      setBusy(true);
      setError('');
      setSaved('');
      try {
        const found = await fetchOptions(machineFor(options, printer, nozzle));
        setOptions(found);
        setDraft((current) =>
          current === null
            ? current
            : {
                ...current,
                printer,
                nozzle,
                // A process the new machine cannot use is replaced by its
                // declared default rather than left showing.
                process: presetFor(found.processes, current.process),
                filament: presetFor(found.filaments, current.filament),
              },
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [options],
  );

  const store = useCallback(async () => {
    if (!options || !draft) return;
    setBusy(true);
    setError('');
    setSaved('');
    const machine = machineFor(options, draft.printer, draft.nozzle);
    const deviation = Number(draft.deviation);
    const settings: SlicerSettings = {
      orient: draft.orient,
      arrange: draft.arrange,
      ...(Number.isFinite(deviation) && deviation > 0 ? { deviation } : {}),
      ...(draft.printer ? { printer: draft.printer } : {}),
      ...(draft.nozzle ? { nozzle: draft.nozzle } : {}),
      ...(machine ? { machine } : {}),
      ...(draft.process ? { process: draft.process } : {}),
      ...(draft.filament ? { filament: draft.filament } : {}),
    };
    try {
      await saveSettings(settings);
      setSaved('Saved. The next slice uses these.');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [options, draft]);

  const printer = options && draft ? printerNamed(options, draft.printer) : undefined;
  const machine = options && draft ? machineFor(options, draft.printer, draft.nozzle) : '';

  return (
    <div className="drawer">
      <div className="panel-title">
        Slicer settings
        <div className="panel-actions">
          <button className="secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
      <div className="drawer-body">
        {error && <div className="drawer-error">{error}</div>}
        {!options && busy && <div className="drawer-note">Reading the slicer's presets…</div>}
        {options && options.printers.length === 0 && (
          <div className="drawer-note">
            No printers were found. The addon reads them from Bambu Studio's own
            configuration, so add a printer there — or set the Slicer* preferences in
            FreeCAD.
          </div>
        )}
        {options && draft && (
          <>
            <label className="drawer-field">
              <span>Printer</span>
              <select
                value={draft.printer}
                disabled={busy}
                onChange={(e) => {
                  const next = e.target.value;
                  refilter(next, nozzleFor(options, next, { nozzle: draft.nozzle }));
                }}
              >
                {options.printers.map((entry) => (
                  <option key={entry.model} value={entry.model}>
                    {entry.model}
                  </option>
                ))}
              </select>
            </label>

            <label className="drawer-field">
              <span>Nozzle</span>
              <select
                value={draft.nozzle}
                disabled={busy}
                onChange={(e) => refilter(draft.printer, e.target.value)}
              >
                {(printer?.nozzles ?? []).map((size) => (
                  <option key={size} value={size}>
                    {size} mm
                  </option>
                ))}
              </select>
            </label>

            <div className="drawer-machine">
              {machine ? `Machine preset: ${machine}` : 'No machine preset is installed for that nozzle.'}
            </div>

            <label className="drawer-field">
              <span>Process</span>
              <select
                value={draft.process}
                disabled={busy}
                onChange={(e) => setDraft({ ...draft, process: e.target.value })}
              >
                {options.processes.map((entry) => (
                  <option key={entry.name} value={entry.name}>
                    {entry.name}
                    {entry.default ? ' (default)' : ''}
                  </option>
                ))}
              </select>
            </label>

            <label className="drawer-field">
              <span>Filament</span>
              <select
                value={draft.filament}
                disabled={busy}
                onChange={(e) => setDraft({ ...draft, filament: e.target.value })}
              >
                {options.filaments.map((entry) => (
                  <option key={entry.name} value={entry.name}>
                    {entry.name}
                    {entry.default ? ' (default)' : ''}
                  </option>
                ))}
              </select>
            </label>

            <label className="drawer-check">
              <input
                type="checkbox"
                checked={draft.orient}
                disabled={busy}
                onChange={(e) => setDraft({ ...draft, orient: e.target.checked })}
              />
              <span>
                Orient each part onto its recorded print direction
                <em>Off exports exactly as modelled, coordinates included.</em>
              </span>
            </label>

            <label className="drawer-check">
              <input
                type="checkbox"
                checked={draft.arrange}
                disabled={busy}
                onChange={(e) => setDraft({ ...draft, arrange: e.target.checked })}
              />
              <span>
                Let the slicer lay the plate out
                <em>It packs better, but toolpath X/Y are then plate coordinates.</em>
              </span>
            </label>

            <label className="drawer-field">
              <span>Mesh deviation</span>
              <input
                type="number"
                min="0.001"
                max="5"
                step="0.01"
                value={draft.deviation}
                disabled={busy}
                onChange={(e) => setDraft({ ...draft, deviation: e.target.value })}
              />
            </label>
            <div className="drawer-note">
              Millimetres. Smaller refines curved faces; larger has no effect past about
              0.05 mm, because angular deflection floors it.
            </div>

            <div className="drawer-buttons">
              <button onClick={store} disabled={busy}>
                Save
              </button>
              {saved && <span className="drawer-saved">{saved}</span>}
            </div>
            {path && <div className="drawer-path">Stored in {path}</div>}
          </>
        )}
      </div>
    </div>
  );
}
