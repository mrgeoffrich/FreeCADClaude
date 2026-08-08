import { useMemo } from 'react';
import * as THREE from 'three';
import { Line } from '@react-three/drei';
import type { FeatureType, ParseResult, TypeBuffers } from '../types';
import { FEATURE_STYLES } from '../featureColors';

interface TopLayerHighlightProps {
  result: ParseResult;
  visible: Record<FeatureType, boolean>;
  topLayer: number;
}

// Redraws just the topmost visible layer as fat (screen-space width) lines in each type's
// colour. Plain THREE lines are locked to 1px, so the current layer is otherwise hard to
// pick out; this overlays it the way slicer previews highlight the active layer.
export function TopLayerHighlight({ result, visible, topLayer }: TopLayerHighlightProps) {
  const { points, colors } = useMemo(() => {
    const layer = Math.max(0, Math.min(topLayer, result.layerCount - 1));
    const pts: [number, number, number][] = [];
    const cols: [number, number, number][] = [];

    for (const [type, buf] of Object.entries(result.byType) as [FeatureType, TypeBuffers][]) {
      if (!buf || !visible[type]) continue;
      const start = buf.layerStart[layer];
      const end = buf.layerStart[layer + 1];
      if (end <= start) continue;
      const c = new THREE.Color(FEATURE_STYLES[type].color);
      for (let v = start; v < end; v++) {
        const o = v * 3;
        pts.push([buf.positions[o], buf.positions[o + 1], buf.positions[o + 2]]);
        cols.push([c.r, c.g, c.b]);
      }
    }
    return { points: pts, colors: cols };
  }, [result, visible, topLayer]);

  // `segments` => each consecutive pair of points is an independent segment (our buffer layout).
  if (points.length < 2) return null;
  return (
    <Line points={points} color="white" vertexColors={colors} segments lineWidth={4.5} />
  );
}
