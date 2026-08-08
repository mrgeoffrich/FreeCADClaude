import { useEffect, useMemo } from 'react';
import * as THREE from 'three';
import type { PredictionBuffers } from '../types';

interface PredictionOverlayProps {
  data: PredictionBuffers;
  visible: boolean;
  range: [number, number];
}

export const PREDICTION_COLOR = '#2dd4ff';

// The Python pipeline's predicted PHYSICAL edge, overlaid on the toolpath and coloured by
// per-vertex deviation from the nominal edge. Layer-sorted like the toolpath, so the same
// layer-slider draw-range applies.
export function PredictionOverlay({ data, visible, range }: PredictionOverlayProps) {
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(data.positions, 3));
    g.setAttribute('color', new THREE.BufferAttribute(data.colors, 3));
    return g;
  }, [data]);

  useEffect(() => () => geometry.dispose(), [geometry]);

  useEffect(() => {
    const lc = data.layerCount;
    const lo = Math.max(0, Math.min(range[0], lc));
    const hi = Math.max(lo, Math.min(range[1] + 1, lc));
    const start = data.layerStart[lo];
    const end = data.layerStart[hi];
    geometry.setDrawRange(start, Math.max(0, end - start));
  }, [geometry, data, range]);

  if (data.count === 0) return null;

  return (
    <lineSegments geometry={geometry} visible={visible} frustumCulled={false} renderOrder={1}>
      <lineBasicMaterial vertexColors />
    </lineSegments>
  );
}
