import { useEffect } from 'react';
import { Canvas, useThree } from '@react-three/fiber';
import { OrbitControls, Grid, OrthographicCamera, PerspectiveCamera } from '@react-three/drei';
import * as THREE from 'three';
import { useViewerStore } from '../state/viewerStore';
import { ToolpathScene } from './ToolpathScene';
import { RetractionPoints } from './RetractionPoints';
import { TopLayerHighlight } from './TopLayerHighlight';
import { PredictionOverlay } from './PredictionOverlay';
import { PredictionTopHighlight } from './PredictionTopHighlight';
import { MeshOverlay } from './MeshOverlay';
import { StlOverlay } from './StlOverlay';

interface Bounds {
  min: [number, number, number];
  max: [number, number, number];
}

interface OrbitLike {
  target: THREE.Vector3;
  update: () => void;
}

// Frames whichever camera is active (perspective or orthographic) to the model and points
// OrbitControls at its centre. Re-runs when the file or projection changes.
function CameraRig({ bounds, resetKey }: { bounds: Bounds; resetKey: string }) {
  const camera = useThree((s) => s.camera);
  const controls = useThree((s) => s.controls) as unknown as OrbitLike | null;
  const viewport = useThree((s) => s.size);

  useEffect(() => {
    const { min, max } = bounds;
    const cx = (min[0] + max[0]) / 2;
    const cy = (min[1] + max[1]) / 2;
    const cz = (min[2] + max[2]) / 2;
    const extent = Math.max(max[0] - min[0], max[1] - min[1], max[2] - min[2], 1);
    const cam = camera as THREE.PerspectiveCamera & THREE.OrthographicCamera;

    if ((cam as THREE.OrthographicCamera).isOrthographicCamera) {
      // Isometric: view down the (1,1,1) diagonal, zoom to fit.
      const d = extent * 3;
      cam.position.set(cx + d, cy + d, cz + d);
      cam.near = 0.01;
      cam.far = extent * 200;
      cam.zoom = Math.min(viewport.width, viewport.height) / (extent * 1.5);
    } else {
      cam.position.set(cx + extent * 0.9, cy + extent * 0.8, cz + extent * 1.4);
      cam.near = Math.max(extent / 1000, 0.01);
      cam.far = extent * 50;
    }
    cam.updateProjectionMatrix();

    if (controls) {
      controls.target.set(cx, cy, cz);
      controls.update();
    }
    // viewport intentionally omitted so resizing doesn't reset the user's framing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey, camera, controls]);

  return null;
}

export function Viewer() {
  const result = useViewerStore((s) => s.result);
  const visible = useViewerStore((s) => s.visible);
  const showGcode = useViewerStore((s) => s.showGcode);
  const showStl = useViewerStore((s) => s.showStl);
  const showRetractions = useViewerStore((s) => s.showRetractions);
  const travelTopLayerOnly = useViewerStore((s) => s.travelTopLayerOnly);
  const prediction = useViewerStore((s) => s.prediction);
  const showPrediction = useViewerStore((s) => s.showPrediction);
  const mesh = useViewerStore((s) => s.mesh);
  const showMesh = useViewerStore((s) => s.showMesh);
  const projection = useViewerStore((s) => s.projection);
  const layerRange = useViewerStore((s) => s.layerRange);
  const fileName = useViewerStore((s) => s.fileName);

  return (
    <Canvas className="canvas" dpr={[1, 2]} gl={{ localClippingEnabled: true }}>
      {projection === 'isometric' ? (
        <OrthographicCamera makeDefault position={[300, 300, 300]} near={0.1} far={20000} zoom={4} />
      ) : (
        <PerspectiveCamera makeDefault position={[120, 120, 120]} fov={45} near={0.1} far={20000} />
      )}
      <color attach="background" args={['#15171c']} />
      <ambientLight intensity={0.7} />
      <directionalLight position={[1, 2, 1]} intensity={0.4} />
      <Grid
        infiniteGrid
        cellSize={10}
        sectionSize={50}
        cellColor="#262b36"
        sectionColor="#3a4252"
        fadeDistance={1600}
        fadeStrength={2}
        position={[0, 0, 0]}
      />
      {result && (
        <>
          {showGcode && (
            <>
              <ToolpathScene
                result={result}
                visible={visible}
                layerRange={layerRange}
                travelTopLayerOnly={travelTopLayerOnly}
              />
              <TopLayerHighlight result={result} visible={visible} topLayer={layerRange[1]} />
            </>
          )}
          {prediction && (
            <>
              <PredictionOverlay data={prediction} visible={showPrediction} range={layerRange} />
              <PredictionTopHighlight
                data={prediction}
                visible={showPrediction}
                topLayer={layerRange[1]}
              />
            </>
          )}
          {mesh && <MeshOverlay data={mesh} visible={showMesh} range={layerRange} />}
          {mesh && mesh.stlIndices.length > 0 && (
            <StlOverlay
              data={mesh}
              visible={showStl}
              topY={result.layerZ[Math.min(layerRange[1], result.layerZ.length - 1)] ?? result.bounds.max[1]}
            />
          )}
          <RetractionPoints
            data={result.retractions}
            visible={showRetractions}
            range={layerRange}
            layerCount={result.layerCount}
          />
          <CameraRig bounds={result.bounds} resetKey={`${fileName ?? ''}-${projection}`} />
        </>
      )}
      <OrbitControls makeDefault enableDamping />
    </Canvas>
  );
}
