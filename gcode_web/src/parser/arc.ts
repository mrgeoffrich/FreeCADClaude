// Flatten a G2/G3 arc move into straight chords. Bambu's arc fitting turns curved walls
// and holes into G2/G3 moves; without flattening they don't render at all (spec.md §6).
//
// Arcs are defined in the XY plane (G17) with I/J centre offsets relative to the start
// point. We subdivide so the chord deviation from the true arc stays under `tolerance`.

export interface Pt {
  x: number;
  y: number;
}

/**
 * Returns the intermediate + end points of the arc (the start point is NOT included).
 * @param start    current nozzle XY
 * @param end      target XY (equal to start for a full circle)
 * @param center   arc centre (start + I,J)
 * @param clockwise true for G2, false for G3
 */
export function flattenArc(
  start: Pt,
  end: Pt,
  center: Pt,
  clockwise: boolean,
  tolerance = 0.05,
  maxSegments = 512,
): Pt[] {
  const r = Math.hypot(start.x - center.x, start.y - center.y);
  if (r < 1e-6) return [end];

  const startAngle = Math.atan2(start.y - center.y, start.x - center.x);
  const endAngle = Math.atan2(end.y - center.y, end.x - center.x);

  // Signed sweep in the direction of travel. CCW (G3) is positive, CW (G2) negative.
  let delta = endAngle - startAngle;
  if (clockwise) {
    // Force delta into (-2π, 0]; a zero/positive value means a full clockwise circle.
    while (delta >= 0) delta -= 2 * Math.PI;
  } else {
    while (delta <= 0) delta += 2 * Math.PI;
  }

  // Chord tolerance -> max angle step. acos arg clamped for tiny radii.
  const maxStep = 2 * Math.acos(Math.max(-1, Math.min(1, 1 - tolerance / r)));
  const segs = Math.max(
    1,
    Math.min(maxSegments, Math.ceil(Math.abs(delta) / Math.max(maxStep, 1e-3))),
  );

  const pts: Pt[] = [];
  for (let i = 1; i <= segs; i++) {
    if (i === segs) {
      // Snap the final point exactly to the commanded end to avoid drift.
      pts.push({ x: end.x, y: end.y });
    } else {
      const a = startAngle + (delta * i) / segs;
      pts.push({ x: center.x + r * Math.cos(a), y: center.y + r * Math.sin(a) });
    }
  }
  return pts;
}
