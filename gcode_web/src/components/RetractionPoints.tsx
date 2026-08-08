import { useEffect, useMemo } from 'react';
import * as THREE from 'three';
import type { PointBuffers } from '../types';

interface RetractionPointsProps {
  data: PointBuffers;
  visible: boolean;
  range: [number, number];
  layerCount: number;
}

// White dots marking where each retraction begins, sized in world units so they scale with
// zoom. depthTest is off so they read as an always-visible overlay, and they share the
// toolpath's layer-sorted layout so the layer slider filters them with a single draw range.
export function RetractionPoints({ data, visible, range, layerCount }: RetractionPointsProps) {
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(data.positions, 3));
    return g;
  }, [data]);

  useEffect(() => () => geometry.dispose(), [geometry]);

  useEffect(() => {
    const lo = Math.max(0, Math.min(range[0], layerCount));
    const hi = Math.max(lo, Math.min(range[1] + 1, layerCount));
    const start = data.layerStart[lo];
    const end = data.layerStart[hi];
    geometry.setDrawRange(start, Math.max(0, end - start));
  }, [geometry, data, range, layerCount]);

  if (data.count === 0) return null;

  return (
    <points geometry={geometry} visible={visible} frustumCulled={false} renderOrder={2}>
      <pointsMaterial
        color="#ffffff"
        size={0.9}
        sizeAttenuation
        depthTest={false}
        transparent
        opacity={0.95}
      />
    </points>
  );
}
