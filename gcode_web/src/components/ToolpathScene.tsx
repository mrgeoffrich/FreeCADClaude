import { useEffect, useMemo } from 'react';
import * as THREE from 'three';
import type { FeatureType, ParseResult, TypeBuffers } from '../types';
import { FEATURE_STYLES } from '../featureColors';

interface TypeLinesProps {
  buf: TypeBuffers;
  color: string;
  visible: boolean;
  range: [number, number];
  layerCount: number;
}

// One LineSegments per feature type. Type visibility is a cheap boolean; the layer band is a
// single contiguous draw range because the buffer is layer-sorted (see TypeBuffers).
function TypeLines({ buf, color, visible, range, layerCount }: TypeLinesProps) {
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(buf.positions, 3));
    return g;
  }, [buf]);

  useEffect(() => () => geometry.dispose(), [geometry]);

  useEffect(() => {
    const lo = Math.max(0, Math.min(range[0], layerCount));
    const hi = Math.max(lo, Math.min(range[1] + 1, layerCount));
    const start = buf.layerStart[lo];
    const end = buf.layerStart[hi];
    geometry.setDrawRange(start, Math.max(0, end - start));
  }, [geometry, buf, range, layerCount]);

  return (
    <lineSegments geometry={geometry} visible={visible} frustumCulled={false}>
      <lineBasicMaterial color={color} />
    </lineSegments>
  );
}

interface ToolpathSceneProps {
  result: ParseResult;
  visible: Record<FeatureType, boolean>;
  layerRange: [number, number];
  travelTopLayerOnly: boolean;
}

export function ToolpathScene({ result, visible, layerRange, travelTopLayerOnly }: ToolpathSceneProps) {
  return (
    <>
      {(Object.entries(result.byType) as [FeatureType, TypeBuffers][]).map(([type, buf]) => {
        if (!buf) return null;
        // Travel can dwarf everything; optionally restrict it to just the top visible layer.
        const range: [number, number] =
          type === 'travel' && travelTopLayerOnly ? [layerRange[1], layerRange[1]] : layerRange;
        return (
          <TypeLines
            key={type}
            buf={buf}
            color={FEATURE_STYLES[type].color}
            visible={visible[type] ?? true}
            range={range}
            layerCount={result.layerCount}
          />
        );
      })}
    </>
  );
}
