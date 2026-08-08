import type { FeatureType } from './types';

export interface FeatureStyle {
  label: string;
  /** Hex colour used for both the legend swatch and the 3D line material. */
  color: string;
  /** Whether the type is visible when a file first loads. Travel is off by default. */
  defaultVisible: boolean;
}

// Single source of truth for label + colour per feature type. Palette loosely mirrors
// Bambu Studio's toolpath preview so it reads familiarly. Unknown is magenta on purpose:
// it makes any unmapped `; FEATURE:` string jump out.
export const FEATURE_STYLES: Record<FeatureType, FeatureStyle> = {
  'outer-wall': { label: 'Outer wall', color: '#ff6a2b', defaultVisible: true },
  'inner-wall': { label: 'Inner wall', color: '#28c76f', defaultVisible: true },
  'overhang-wall': { label: 'Overhang wall', color: '#22d3ee', defaultVisible: true },
  'floating-vertical-shell': { label: 'Floating vertical shell', color: '#14b8a6', defaultVisible: true },
  'top-surface': { label: 'Top surface', color: '#ff5d8f', defaultVisible: true },
  'bottom-surface': { label: 'Bottom surface', color: '#c2185b', defaultVisible: true },
  'internal-solid-infill': { label: 'Internal solid infill', color: '#e0413f', defaultVisible: true },
  'sparse-infill': { label: 'Sparse infill', color: '#d9a521', defaultVisible: true },
  'gap-infill': { label: 'Gap infill', color: '#a855f7', defaultVisible: true },
  bridge: { label: 'Bridge', color: '#3b82f6', defaultVisible: true },
  support: { label: 'Support', color: '#7a8aa3', defaultVisible: true },
  'support-interface': { label: 'Support interface', color: '#a9b6c9', defaultVisible: true },
  custom: { label: 'Custom / skirt / brim', color: '#9aa0aa', defaultVisible: true },
  travel: { label: 'Travel', color: '#5a6068', defaultVisible: false },
  unknown: { label: 'Unknown', color: '#ff00ff', defaultVisible: true },
};

// Lowercased `; FEATURE:` (or legacy `;TYPE:`) string -> FeatureType. Includes a few
// Orca/PrusaSlicer aliases so non-Bambu files degrade gracefully.
const FEATURE_ALIASES: Record<string, FeatureType> = {
  'outer wall': 'outer-wall',
  'external perimeter': 'outer-wall',
  'inner wall': 'inner-wall',
  perimeter: 'inner-wall',
  'overhang wall': 'overhang-wall',
  'overhang perimeter': 'overhang-wall',
  'floating vertical shell': 'floating-vertical-shell',
  'top surface': 'top-surface',
  'top solid infill': 'top-surface',
  'bottom surface': 'bottom-surface',
  'internal solid infill': 'internal-solid-infill',
  'solid infill': 'internal-solid-infill',
  'sparse infill': 'sparse-infill',
  'internal infill': 'sparse-infill',
  infill: 'sparse-infill',
  'gap infill': 'gap-infill',
  'gap fill': 'gap-infill',
  bridge: 'bridge',
  'bridge infill': 'bridge',
  'internal bridge': 'bridge',
  support: 'support',
  'support material': 'support',
  'support interface': 'support-interface',
  'support material interface': 'support-interface',
  custom: 'custom',
  skirt: 'custom',
  brim: 'custom',
  'skirt/brim': 'custom',
};

/** Map a raw slicer feature string to a FeatureType, or `null` if unrecognised. */
export function normaliseFeature(raw: string): FeatureType | null {
  return FEATURE_ALIASES[raw.trim().toLowerCase()] ?? null;
}
