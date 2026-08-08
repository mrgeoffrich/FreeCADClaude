// SPDX-License-Identifier: LGPL-2.1-or-later
// Local patch (FreeCADClaude) -- see VENDORED.md.
//
// The slicer settings the addon's server offers, and the derivations the drawer
// makes from them. Split out of the component so the choosing rules can be
// tested without a browser: which printer and nozzle a stored document means,
// and what happens to a process or filament that the newly chosen machine
// cannot use.
//
// The fetches carry no token. The page was loaded with `?t=` and the server set
// an HttpOnly cookie on that response, so a same-origin request authenticates
// itself.

export interface PrinterOption {
  model: string;
  vendor: string;
  nozzles: string[];
  /** nozzle size -> the machine preset name for it, where one is installed. */
  machines: Record<string, string>;
  materials: string[];
}

export interface PresetOption {
  name: string;
  /** Whether the chosen machine declares this as its own default. */
  default: boolean;
}

export interface SlicerOptions {
  printers: PrinterOption[];
  selected: { machine: string | null; process: string | null; filament: string | null };
  machine: string | null;
  processes: PresetOption[];
  filaments: PresetOption[];
  nozzle_default: string;
}

export interface SlicerSettings {
  printer?: string;
  nozzle?: string;
  machine?: string;
  process?: string;
  filament?: string;
  orient?: boolean;
  arrange?: boolean;
  deviation?: number;
}

async function readOrThrow(response: Response): Promise<unknown> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // A reply with no JSON body: one failure, not two.
  }
  if (!response.ok) {
    // The server's own sentence, which names what it refused and lists what is
    // installed instead. Far more use than the status code.
    const stated = (body as { error?: string } | null)?.error;
    throw new Error(stated || `the server answered ${response.status}`);
  }
  return body;
}

/** The printers, nozzles and the presets `machine` can use. */
export async function fetchOptions(machine?: string): Promise<SlicerOptions> {
  const query = machine ? `?machine=${encodeURIComponent(machine)}` : '';
  return (await readOrThrow(await fetch(`/api/slicer/options${query}`))) as SlicerOptions;
}

/** What is stored in slicer.json today, plus where that file is. */
export async function fetchSettings(): Promise<{ settings: SlicerSettings; path: string }> {
  const body = (await readOrThrow(await fetch('/api/slicer/config'))) as {
    settings: SlicerSettings;
    path: string;
  };
  return { settings: body.settings ?? {}, path: body.path ?? '' };
}

/** Replace slicer.json. Rejects with the server's reason if a name is unknown. */
export async function saveSettings(settings: SlicerSettings): Promise<SlicerSettings> {
  const body = (await readOrThrow(
    await fetch('/api/slicer/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    }),
  )) as { settings: SlicerSettings };
  return body.settings ?? settings;
}

export function printerNamed(options: SlicerOptions, model?: string): PrinterOption | undefined {
  return options.printers.find((p) => p.model === model);
}

/**
 * Which printer the drawer should show: the stored one, else whichever printer
 * owns the machine preset the server filtered for, else the first added.
 */
export function printerFor(options: SlicerOptions, settings: SlicerSettings): string {
  if (printerNamed(options, settings.printer)) return settings.printer as string;
  const owner = options.printers.find((p) =>
    Object.values(p.machines).includes(options.machine ?? ''),
  );
  return owner?.model ?? options.printers[0]?.model ?? '';
}

/**
 * Which nozzle to show for `printer`: the stored one if that printer has it,
 * else the one whose machine preset is the filtered one, else the default (0.4)
 * if it is offered, else the first.
 */
export function nozzleFor(
  options: SlicerOptions,
  printer: string,
  settings: SlicerSettings,
): string {
  const entry = printerNamed(options, printer);
  const nozzles = entry?.nozzles ?? [];
  if (settings.nozzle && nozzles.includes(settings.nozzle)) return settings.nozzle;
  const filtered = Object.entries(entry?.machines ?? {}).find(
    ([, machine]) => machine === options.machine,
  );
  if (filtered) return filtered[0];
  if (nozzles.includes(options.nozzle_default)) return options.nozzle_default;
  return nozzles[0] ?? '';
}

/** The machine preset for a printer at a nozzle, or '' if none is installed. */
export function machineFor(options: SlicerOptions, printer: string, nozzle: string): string {
  return printerNamed(options, printer)?.machines[nozzle] ?? '';
}

/**
 * Which entry of `offered` to select: the current choice if it survived the
 * filter, else whichever the machine declares as its default, else the first.
 *
 * This is what a nozzle change does to a process: changing nozzle re-derives
 * all three presets, so a process that the new machine cannot use has to be
 * replaced rather than left showing. Storing it would be refused anyway.
 */
export function presetFor(offered: PresetOption[], chosen?: string): string {
  if (chosen && offered.some((entry) => entry.name === chosen)) return chosen;
  return (offered.find((entry) => entry.default) ?? offered[0])?.name ?? '';
}
