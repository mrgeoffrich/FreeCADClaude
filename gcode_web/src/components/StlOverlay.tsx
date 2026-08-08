import { useEffect, useMemo } from 'react';
import * as THREE from 'three';
import type { MeshBuffers } from '../types';

interface StlOverlayProps {
  data: MeshBuffers;
  visible: boolean;
  /** World Y (= print Z) of the top visible layer; the STL is clipped above this. */
  topY: number;
}

// The registered source STL, drawn as a translucent grey reference so you can eyeball the
// predicted surface against the design. Clipped to the current layer slice so it matches the
// rest of the scene.
export function StlOverlay({ data, visible, topY }: StlOverlayProps) {
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(data.stlPositions, 3));
    g.setIndex(new THREE.BufferAttribute(data.stlIndices, 1));
    g.computeVertexNormals();
    return g;
  }, [data]);

  // One clipping plane that keeps geometry at/below the top visible layer (world Y <= topY).
  const plane = useMemo(() => new THREE.Plane(new THREE.Vector3(0, -1, 0), 0), []);
  useEffect(() => {
    plane.constant = topY;
  }, [plane, topY]);

  useEffect(() => () => geometry.dispose(), [geometry]);

  if (data.stlIndices.length === 0) return null;

  return (
    <mesh geometry={geometry} visible={visible} frustumCulled={false}>
      <meshStandardMaterial
        color="#aab4c4"
        transparent
        opacity={0.3}
        roughness={1}
        metalness={0}
        flatShading
        side={THREE.DoubleSide}
        depthWrite={false}
        clippingPlanes={[plane]}
      />
    </mesh>
  );
}
