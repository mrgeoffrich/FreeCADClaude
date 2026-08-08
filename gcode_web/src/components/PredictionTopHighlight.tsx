import { useMemo } from 'react';
import { Line } from '@react-three/drei';
import type { PredictionBuffers } from '../types';

interface Props {
  data: PredictionBuffers;
  visible: boolean;
  topLayer: number;
}

// Fat-line highlight of the top visible layer's predicted edge — same treatment as the
// toolpath's TopLayerHighlight, keeping the per-vertex deviation colours.
export function PredictionTopHighlight({ data, visible, topLayer }: Props) {
  const { points, colors } = useMemo(() => {
    const L = Math.max(0, Math.min(topLayer, data.layerCount - 1));
    const start = data.layerStart[L];
    const end = data.layerStart[L + 1];
    const pts: [number, number, number][] = [];
    const cols: [number, number, number][] = [];
    for (let v = start; v < end; v++) {
      const o = v * 3;
      pts.push([data.positions[o], data.positions[o + 1], data.positions[o + 2]]);
      cols.push([data.colors[o], data.colors[o + 1], data.colors[o + 2]]);
    }
    return { points: pts, colors: cols };
  }, [data, topLayer]);

  if (!visible || points.length < 2) return null;
  return <Line points={points} vertexColors={colors} segments lineWidth={4.5} />;
}
