import { useEffect, useMemo } from 'react';
import * as THREE from 'three';
import type { MeshBuffers } from '../types';

interface MeshOverlayProps {
  data: MeshBuffers;
  visible: boolean;
  range: [number, number];
}

// The predicted 3D surface, vertex-coloured by signed distance to the STL. Indexed geometry;
// the layer slider draw-ranges the index buffer (faces are layer-sorted). Lightly lit so the
// form reads while the deviation colours stay dominant.
export function MeshOverlay({ data, visible, range }: MeshOverlayProps) {
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(data.positions, 3));
    g.setAttribute('color', new THREE.BufferAttribute(data.colors, 3));
    g.setIndex(new THREE.BufferAttribute(data.indices, 1));
    g.computeVertexNormals();
    return g;
  }, [data]);

  useEffect(() => () => geometry.dispose(), [geometry]);

  useEffect(() => {
    const lc = data.layerCount;
    const lo = Math.max(0, Math.min(range[0], lc));
    const hi = Math.max(lo, Math.min(range[1] + 1, lc));
    const start = data.indexLayerStart[lo];
    const end = data.indexLayerStart[hi];
    geometry.setDrawRange(start, Math.max(0, end - start));
  }, [geometry, data, range]);

  if (data.vertexCount === 0) return null;

  return (
    <mesh geometry={geometry} visible={visible} frustumCulled={false}>
      <meshStandardMaterial
        vertexColors
        flatShading
        roughness={0.9}
        metalness={0}
        side={THREE.DoubleSide}
      />
    </mesh>
  );
}
