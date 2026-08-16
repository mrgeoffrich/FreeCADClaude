// SPDX-License-Identifier: LGPL-2.1-or-later
// The face-markup model viewer: load the real BRep geometry of the active
// FreeCAD document into a browser scene and let the user orbit it.
//
// Data flow (per the plan's invariant 4 -- the WASM kernel and the page load
// once per tab, publishes arrive over SSE):
//
//   mount ──▶ GET /api/latest ──parseLatest──▶ publish? ──▶ fetch each .brp
//   EventSource("/api/events") ──published──▶ replace the scene's meshes
//
// Each object's .brp bytes go to the occt-import-js worker, which
// tessellates off the main thread and posts back position/normal/index
// arrays; one THREE.BufferGeometry per FreeCAD object, tagged with the
// object's name, grey, lit by a fixed ambient + directional pair. That is
// the whole feature this phase proves: "can the user see the actual solid".
// No picking, no upload, no settings -- those are later phases.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useThree } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import { Box3, BufferAttribute, BufferGeometry, MeshStandardMaterial, Vector3 } from 'three';
import { parseLatest, parsePublishedEventData, type PublishedModel } from './api';
import type { ParseRequest, ParsedPayload, WorkerReply } from './worker';

/** One tessellated FreeCAD object, ready to draw. */
interface LoadedMesh {
  /** The FreeCAD object name the publish carried; tags the mesh for later
   * phases (picking resolves a face back to this object). */
  name: string;
  geometry: BufferGeometry;
}

type Status =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; names: string[] }
  | { kind: 'error'; message: string };

// -- the worker, wrapped in promises ---------------------------------------

/** One parse request in flight: the correlation id maps to its promise. */
function useParseWorker(): (buffer: ArrayBuffer) => Promise<ParsedPayload> {
  const workerRef = useRef<Worker | null>(null);
  const pendingRef = useRef(new Map<number, { resolve: (p: ParsedPayload) => void; reject: (e: Error) => void }>());
  const nextIdRef = useRef(1);

  return useCallback((buffer: ArrayBuffer) => {
    let worker = workerRef.current;
    if (!worker) {
      worker = new Worker(new URL('./worker.ts', import.meta.url), { type: 'module' });
      worker.onmessage = (event: MessageEvent<WorkerReply>) => {
        const reply = event.data;
        const pending = pendingRef.current.get(reply.id);
        if (!pending) return; // a reply for a request this side already gave up on
        pendingRef.current.delete(reply.id);
        if (reply.kind === 'parsed') pending.resolve(reply);
        else pending.reject(new Error(reply.message));
      };
      worker.onerror = () => {
        // The worker script or the WASM kernel failed to load at all; every
        // request still waiting is stuck, so fail them rather than hang the
        // loading state on a promise that can never settle.
        const pending = pendingRef.current;
        pendingRef.current = new Map();
        for (const entry of pending.values()) entry.reject(new Error('the model worker failed to start'));
      };
      workerRef.current = worker;
    }
    const id = nextIdRef.current++;
    return new Promise<ParsedPayload>((resolve, reject) => {
      pendingRef.current.set(id, { resolve, reject });
      const request: ParseRequest = { kind: 'parse', id, buffer };
      // The ArrayBuffer is transferred: the worker owns the bytes from here
      // on, and the fetch buffer costs nothing to move.
      worker!.postMessage(request, [buffer]);
    });
  }, []);
}

// -- camera framing --------------------------------------------------------

/** One-time framing of the freshly loaded set: centre the model, stand the
 * camera far enough back to see all of it, point the orbit pivot at it. A
 * fixed sensible default -- there is deliberately no camera UI in this
 * phase, only the orbit itself. Runs whenever the loaded set changes. */
function FrameCamera({ box }: { box: Box3 | null }) {
  const camera = useThree((state) => state.camera);
  const controls = useThree((state) => state.controls) as unknown as
    | { target: Vector3; update: () => void }
    | null;

  useEffect(() => {
    if (!box) return;
    const center = box.getCenter(new Vector3());
    const size = box.getSize(new Vector3());
    const radius = Math.max(size.x, size.y, size.z) / 2;
    if (!(radius > 0)) return;
    const distance = radius * 3.2;
    camera.position.set(
      center.x + distance * 0.75,
      center.y + distance * 0.75,
      center.z + distance * 0.75,
    );
    // The camera is the renderer's own mutable object, not hook state -- R3F
    // hands it out for exactly this kind of imperative framing.
    /* eslint-disable react-hooks/immutability */
    camera.near = Math.max(radius / 1000, 1e-6);
    camera.far = Math.max(radius * 100, 10);
    /* eslint-enable react-hooks/immutability */
    camera.updateProjectionMatrix();
    camera.lookAt(center);
    if (controls) {
      controls.target.copy(center);
      controls.update();
    }
  }, [box, camera, controls]);

  return null;
}

// -- the app ---------------------------------------------------------------

export function App() {
  const parseInWorker = useParseWorker();
  const [meshes, setMeshes] = useState<LoadedMesh[]>([]);
  const [status, setStatus] = useState<Status>({ kind: 'idle' });
  // Monotonic per-publish sequence: a publish that arrives while an earlier
  // one is still loading supersedes it, and the earlier one's results are
  // discarded rather than replacing the newer scene out of order.
  const loadSeqRef = useRef(0);

  const loadPublished = useCallback(
    async (published: PublishedModel) => {
      const seq = ++loadSeqRef.current;
      setStatus({ kind: 'loading' });
      const names = Object.keys(published.objects);
      try {
        const loaded: LoadedMesh[] = [];
        // Objects are parsed one at a time: the OCCT instance is stateful and
        // the worker is single-threaded, so sequencing is the honest shape.
        for (const name of names) {
          const entry = published.objects[name];
          const response = await fetch(entry.url);
          if (!response.ok) throw new Error(`HTTP ${response.status} loading ${name}`);
          const parsed = await parseInWorker(await response.arrayBuffer());
          if (seq !== loadSeqRef.current) return; // superseded; discard
          const geometry = new BufferGeometry();
          geometry.setAttribute('position', new BufferAttribute(parsed.positions, 3));
          if (parsed.normals) {
            geometry.setAttribute('normal', new BufferAttribute(parsed.normals, 3));
          } else {
            geometry.computeVertexNormals();
          }
          geometry.setIndex(new BufferAttribute(parsed.indices, 1));
          geometry.computeBoundingSphere();
          loaded.push({ name, geometry });
        }
        if (seq !== loadSeqRef.current) return;
        setMeshes((previous) => {
          for (const mesh of previous) mesh.geometry.dispose();
          return loaded;
        });
        setStatus({ kind: 'ready', names });
      } catch (error) {
        if (seq !== loadSeqRef.current) return;
        // The previous scene stays on screen; the error is a status line, not
        // a blank canvas.
        setStatus({ kind: 'error', message: error instanceof Error ? error.message : String(error) });
      }
    },
    [parseInWorker],
  );

  // One fetch of /api/latest on mount (a tab opened against an already
  // running server starts with what is published), then a standing SSE
  // subscription for the publishes that follow -- the path by which a second
  // view_model_3d call pushes new geometry into this already-open tab without
  // reloading the page.
  useEffect(() => {
    let closed = false;
    void (async () => {
      try {
        const response = await fetch('/api/latest');
        if (!response.ok) return; // 403/404: nothing to load; the idle state is the message
        const published = parseLatest(await response.json());
        if (published && !closed) await loadPublished(published);
      } catch {
        // No connection (server stopped between page load and here): the idle
        // state is the honest one, and the SSE stream will catch a restart.
      }
    })();
    const stream = new EventSource('/api/events');
    stream.addEventListener('published', (event) => {
      const published = parsePublishedEventData((event as MessageEvent).data);
      if (published) void loadPublished(published);
    });
    return () => {
      closed = true;
      stream.close();
    };
  }, [loadPublished]);

  // The union box of the loaded set, for the camera framing. Rebuilt only
  // when the set changes.
  const frameBox = useMemo(() => {
    const box = new Box3();
    for (const mesh of meshes) {
      const attribute = mesh.geometry.getAttribute('position');
      if (attribute) box.union(new Box3().setFromBufferAttribute(attribute as BufferAttribute));
    }
    return box.isEmpty() ? null : box;
  }, [meshes]);

  // One material for every mesh: a single neutral engineering grey, flat
  // shaded. Deliberately no per-object colour, no texture, no material
  // controls -- this whole feature is grey-only, like every other capture
  // tool in the addon.
  const material = useMemo(
    () => new MeshStandardMaterial({ color: '#9e9e9e', flatShading: true, roughness: 1, metalness: 0 }),
    [],
  );

  let statusText: string;
  switch (status.kind) {
    case 'idle':
      statusText = 'No model loaded';
      break;
    case 'loading':
      statusText = 'Loading model…';
      break;
    case 'ready':
      statusText = status.names.join(' · ');
      break;
    case 'error':
      statusText = `Couldn't load the model: ${status.message}`;
      break;
  }

  return (
    <div className="app">
      <Canvas
        className="canvas"
        camera={{ position: [5, 5, 5], fov: 40, near: 0.01, far: 1000 }}
      >
        <color attach="background" args={['#15171c']} />
        <ambientLight intensity={0.6} />
        <directionalLight position={[5, 8, 6]} intensity={1.3} />
        <group>
          {meshes.map((mesh) => (
            <mesh key={mesh.name} name={mesh.name} geometry={mesh.geometry} material={material} />
          ))}
        </group>
        <OrbitControls makeDefault />
        <FrameCamera box={frameBox} />
      </Canvas>
      <div className="statusbar" role="status">
        {statusText}
      </div>
    </div>
  );
}
