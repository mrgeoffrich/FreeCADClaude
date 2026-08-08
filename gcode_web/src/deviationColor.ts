// Diverging colormap for predicted-vs-nominal/STL deviation, shared by the edge overlay and
// the predicted mesh. A full inspection-style rainbow (blue = pulled in / undersized ->
// cyan -> green = on target -> yellow -> red = bulged out / oversized) for maximum colour
// variance across the range. Deviation is clamped to ±scale (mm).

export type RGB = [number, number, number];

export const NEUTRAL: RGB = [0.75, 0.78, 0.82];

// Stops from t=-1 (undersized) through t=0 (on target) to t=+1 (oversized).
const STOPS: RGB[] = [
  [0.16, 0.36, 1.0], // blue
  [0.1, 0.85, 0.95], // cyan
  [0.33, 0.85, 0.35], // green
  [1.0, 0.85, 0.2], // yellow
  [0.95, 0.2, 0.18], // red
];

function lerp(a: RGB, b: RGB, t: number): RGB {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

export function deviationColor(dev: number | undefined, scale: number): RGB {
  if (dev === undefined || Number.isNaN(dev)) return NEUTRAL;
  const t = Math.max(-1, Math.min(1, dev / (scale || 1))); // [-1, 1]
  const u = ((t + 1) / 2) * (STOPS.length - 1); // [0, stops-1]
  const i = Math.max(0, Math.min(STOPS.length - 2, Math.floor(u)));
  return lerp(STOPS[i], STOPS[i + 1], u - i);
}

/** CSS gradient used for the legend colour-bar / swatch (matches the STOPS above). */
export const DEVIATION_GRADIENT =
  'linear-gradient(90deg,#295cff,#1ad9f2,#54d959,#ffd933,#f23330)';
