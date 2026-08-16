// SPDX-License-Identifier: LGPL-2.1-or-later
// The face-markup model viewer: load the real BRep geometry of the active
// FreeCAD document into a browser scene, orbit it, and mark exact faces of
// it -- the Phase 4 surface on top of Phase 3's viewer.
//
// Data flow (per the plan's invariant 4 -- the WASM kernel and the page load
// once per tab, publishes arrive over SSE):
//
//   mount ──▶ GET /api/latest ──parseLatest──▶ publish? ──▶ fetch each .brp
//   EventSource("/api/events") ──published──▶ replace the scene's meshes
//
// Each object's .brp bytes go to the occt-import-js worker, which
// tessellates off the main thread and posts back position/normal/index
// arrays plus the per-face triangle ranges (brep_faces); one THREE mesh per
// FreeCAD object, tagged with the object's name, grey, lit by a fixed
// ambient + directional pair.
//
// Picking (Phase 4): a raycast hit gives a triangle index
// (intersection.faceIndex), which picking.ts resolves to a face ordinal via
// the object's brep_faces ranges. Clicking toggles a FaceMark on that face;
// hovering tints the face under the cursor. Marks and the caption are sent
// as one ModelMarkupDoc to POST /api/upload. Marks are deliberately NOT
// cleared by a successful send -- the same correction the device-annotation
// feature had to make after user feedback -- and are reset only when a
// genuinely new publish (a different id) loads.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useThree, type ThreeEvent } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import { Box3, BufferAttribute, BufferGeometry, MeshStandardMaterial, Vector3 } from 'three';
import { parseLatest, parsePublishedEventData, type PublishedModel } from './api';
import { buildDoc, serializeDoc, type FaceMark } from './doc';
import { faceRangeOf, resolveFaceOrdinal } from './picking';
import type { FaceRange, ParseRequest, ParsedPayload, WorkerReply } from './worker';

/** One tessellated FreeCAD object, ready to draw. */
interface LoadedMesh {
  /** The FreeCAD object name the publish carried; tags the mesh for picking
   * (a picked face resolves back to this object). */
  name: string;
  geometry: BufferGeometry;
  /** The worker's merged per-face triangle ranges, in face order, into the
   * geometry's triangle space (the geometry is made non-indexed below, which
   * preserves triangle order, so the ranges stay valid). */
  brepFaces: FaceRange[];
}

/** The face under the cursor: which object, which BRep face ordinal. */
interface HoveredFace {
  object: string;
  faceIndex: number;
}

type Status =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; names: string[] }
  | { kind: 'error'; message: string };

// -- highlight colours ------------------------------------------------------
// Vertex colours, baked into the geometry; the material is white with
// vertexColors on, so these ARE the lit base colour. The base is the same
// engineering grey the Phase 3 material used. Hover is a subtle light
// blue-grey tint; a marked face is a saturated orange that reads as "picked"
// at a glance against both the grey model and the hover tint.

/** The base grey: #9e9e9e, exactly the Phase 3 material colour. */
const BASE_COLOR: readonly [number, number, number] = [0.62, 0.62, 0.62];
/** Hover: lightly highlighted, deliberately not saturated. */
const HOVER_COLOR: readonly [number, number, number] = [0.82, 0.85, 0.93];
/** Marked: clearly distinct, saturated. */
const MARK_COLOR: readonly [number, number, number] = [1.0, 0.55, 0.12];

/** Repaint one mesh's vertex colours from the marks + hover state: base
 * everywhere, then marked faces, then the hovered face (unless it is already
 * marked -- the mark is the stronger signal). Mutates the colour attribute in
 * place; cheap enough to run on every hover change. */
function paintFaceColors(mesh: LoadedMesh, marks: readonly FaceMark[], hovered: HoveredFace | null) {
  const attribute = mesh.geometry.getAttribute('color');
  if (!attribute) return;
  const colors = attribute.array as Float32Array;
  const vertexCount = colors.length / 3;
  for (let i = 0; i < vertexCount; i++) {
    colors[i * 3] = BASE_COLOR[0];
    colors[i * 3 + 1] = BASE_COLOR[1];
    colors[i * 3 + 2] = BASE_COLOR[2];
  }
  // Triangle t of the (non-indexed) geometry owns vertices [3t, 3t+3).
  const paintFace = (faceIndex: number, color: readonly [number, number, number]) => {
    const range = faceRangeOf(mesh.brepFaces, faceIndex);
    if (!range) return;
    for (let t = range.first; t <= range.last; t++) {
      const base = t * 9;
      for (let v = 0; v < 3; v++) {
        colors[base + v * 3] = color[0];
        colors[base + v * 3 + 1] = color[1];
        colors[base + v * 3 + 2] = color[2];
      }
    }
  };
  for (const mark of marks) {
    if (mark.object === mesh.name) paintFace(mark.face_index, MARK_COLOR);
  }
  if (hovered && hovered.object === mesh.name) {
    const alreadyMarked = marks.some(
      (mark) => mark.object === mesh.name && mark.face_index === hovered.faceIndex,
    );
    if (!alreadyMarked) paintFace(hovered.faceIndex, HOVER_COLOR);
  }
  attribute.needsUpdate = true;
}

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
  const [marks, setMarks] = useState<FaceMark[]>([]);
  const [hovered, setHovered] = useState<HoveredFace | null>(null);
  const [noteForNext, setNoteForNext] = useState('');
  const [caption, setCaption] = useState('');
  const [sending, setSending] = useState(false);
  const [sendStatus, setSendStatus] = useState('');
  const [status, setStatus] = useState<Status>({ kind: 'idle' });
  // The publish id the currently-loaded scene was drawn against. Load-bearing:
  // the sent document's source.publish_id is exactly this, and a later phase
  // resolves marks back to live FreeCAD objects through it.
  const [sourceId, setSourceId] = useState<string | null>(null);
  const sourceIdRef = useRef<string | null>(null);
  // Monotonic per-publish sequence: a publish that arrives while an earlier
  // one is still loading supersedes it, and the earlier one's results are
  // discarded rather than replacing the newer scene out of order.
  const loadSeqRef = useRef(0);
  // Monotonic mark id source ("m1", "m2", ...), purely so the marks list can
  // tell two marks apart; the server never interprets the id.
  const markSeqRef = useRef(1);

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
          // Make the geometry non-indexed so every triangle owns its three
          // vertices: per-face vertex colours (hover/mark highlights) need a
          // vertex to belong to exactly one face, and shared vertices between
          // faces would otherwise get conflicting colours. Triangle order is
          // preserved, so the worker's brep_faces ranges stay valid.
          // toNonIndexed RETURNS the new geometry (it does not mutate this
          // one), so the returned geometry is what gets the colour attribute
          // and what the mesh renders.
          const flat = geometry.toNonIndexed();
          const vertexCount = flat.getAttribute('position').count;
          const colors = new Float32Array(vertexCount * 3);
          for (let i = 0; i < vertexCount; i++) {
            colors[i * 3] = BASE_COLOR[0];
            colors[i * 3 + 1] = BASE_COLOR[1];
            colors[i * 3 + 2] = BASE_COLOR[2];
          }
          flat.setAttribute('color', new BufferAttribute(colors, 3));
          flat.computeBoundingSphere();
          loaded.push({ name, geometry: flat, brepFaces: parsed.brepFaces });
        }
        if (seq !== loadSeqRef.current) return;
        setMeshes((previous) => {
          for (const mesh of previous) mesh.geometry.dispose();
          return loaded;
        });
        // A genuinely new publish resets the mark set; the same publish id
        // arriving again (a remount re-fetching /api/latest) keeps the user's
        // marks. Marks are deliberately NOT cleared by Send -- only this.
        if (sourceIdRef.current !== published.id) {
          sourceIdRef.current = published.id;
          setSourceId(published.id);
          setMarks([]);
          setSendStatus('');
        }
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

  // Repaint highlights whenever the scene, the marks or the hovered face
  // changes. Hover changes are frequent (every face the cursor crosses), so
  // the paint is a cheap in-place refill of the existing colour buffer.
  useEffect(() => {
    for (const mesh of meshes) paintFaceColors(mesh, marks, hovered);
  }, [meshes, marks, hovered]);

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

  // One material for every mesh: white, with the grey baked into the vertex
  // colours -- the colour attribute carries the base grey, the hover tint and
  // the marked-face highlight, so a single material serves all three states.
  const material = useMemo(
    () => new MeshStandardMaterial({ color: '#ffffff', vertexColors: true, flatShading: true, roughness: 1, metalness: 0 }),
    [],
  );

  // -- picking -------------------------------------------------------------

  /** Click on a face toggles its mark: an unmarked face gets a new mark
   * (with the note typed in the panel, if any), an already-marked face loses
   * its mark. Toggle is the consistent choice -- the marks list is where a
   * mark's note is edited after the fact. */
  const handleSceneClick = (event: ThreeEvent<MouseEvent>) => {
    // A drag that ends over the model fires a click too; the pointer travelled
    // during an orbit, so this was a rotation, not a pick.
    if (event.delta > 5) return;
    if (event.faceIndex === null || event.faceIndex === undefined) return;
    const objectName = event.object.userData.objectName as string | undefined;
    const brepFaces = event.object.userData.brepFaces as FaceRange[] | undefined;
    if (!objectName || !brepFaces) return;
    const faceIndex = resolveFaceOrdinal(brepFaces, event.faceIndex);
    if (faceIndex === null) return;
    const existing = marks.find((mark) => mark.object === objectName && mark.face_index === faceIndex);
    if (existing) {
      setMarks((previous) => previous.filter((mark) => mark.id !== existing.id));
      return;
    }
    const mark: FaceMark = {
      id: `m${markSeqRef.current++}`,
      object: objectName,
      face_index: faceIndex,
      color: null,
      note: noteForNext,
    };
    setMarks((previous) => [...previous, mark]);
    setNoteForNext('');
  };

  const handleSceneMove = (event: ThreeEvent<PointerEvent>) => {
    if (event.faceIndex === null || event.faceIndex === undefined) {
      setHovered(null);
      return;
    }
    const objectName = event.object.userData.objectName as string | undefined;
    const brepFaces = event.object.userData.brepFaces as FaceRange[] | undefined;
    if (!objectName || !brepFaces) {
      setHovered(null);
      return;
    }
    const faceIndex = resolveFaceOrdinal(brepFaces, event.faceIndex);
    if (faceIndex === null) {
      setHovered(null);
      return;
    }
    // Return the SAME object when nothing changed, so React bails out and the
    // colour repaint only runs when the hovered face actually moved.
    setHovered((previous) =>
      previous && previous.object === objectName && previous.faceIndex === faceIndex ? previous : { object: objectName, faceIndex },
    );
  };

  const updateMarkNote = (id: string, note: string) => {
    setMarks((previous) => previous.map((mark) => (mark.id === id ? { ...mark, note } : mark)));
  };

  const removeMark = (id: string) => {
    setMarks((previous) => previous.filter((mark) => mark.id !== id));
  };

  // -- send ----------------------------------------------------------------

  /** POST the ModelMarkupDoc to /api/upload as a plain JSON body. Success is
   * confirmed in the panel's status line. Marks are NOT cleared here --
   * deliberately: the user keeps seeing what they sent and clears marks
   * themselves (or a new publish resets them), the same correction the
   * device-annotation feature had to make after user feedback. */
  const handleSend = async () => {
    if (!sourceId || sending) return;
    setSending(true);
    setSendStatus('');
    try {
      const doc = buildDoc({ publishId: sourceId, marks, caption });
      const response = await fetch('/api/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: serializeDoc(doc),
      });
      if (response.ok) {
        setSendStatus(`Sent — ${marks.length} mark${marks.length === 1 ? '' : 's'} on publish ${sourceId}`);
      } else {
        setSendStatus(`Send failed: HTTP ${response.status}`);
      }
    } catch (error) {
      setSendStatus(`Send failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSending(false);
    }
  };

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
        <group
          onClick={handleSceneClick}
          onPointerMove={handleSceneMove}
          onPointerOut={() => setHovered(null)}
        >
          {meshes.map((mesh) => (
            <mesh
              key={mesh.name}
              name={mesh.name}
              userData={{ objectName: mesh.name, brepFaces: mesh.brepFaces }}
              geometry={mesh.geometry}
              material={material}
            />
          ))}
        </group>
        <OrbitControls makeDefault />
        <FrameCamera box={frameBox} />
      </Canvas>
      <div className="panel">
        <div className="panel-title">Face marks</div>
        <div className="panel-row">
          <input
            className="input"
            placeholder="Note for the next mark…"
            value={noteForNext}
            onChange={(event) => setNoteForNext(event.target.value)}
          />
        </div>
        <div className="panel-row">
          <input
            className="input"
            placeholder="Caption…"
            value={caption}
            onChange={(event) => setCaption(event.target.value)}
          />
          <button
            className="button"
            onClick={() => void handleSend()}
            disabled={!sourceId || sending}
          >
            {sending ? 'Sending…' : 'Send'}
          </button>
        </div>
        {marks.length > 0 && (
          <ul className="marks">
            {marks.map((mark) => (
              <li key={mark.id} className="mark">
                <div className="mark-head">
                  <span className="mark-label">
                    {mark.object} · Face{mark.face_index + 1}
                  </span>
                  <button className="button mark-remove" onClick={() => removeMark(mark.id)}>
                    Remove
                  </button>
                </div>
                <input
                  className="input mark-note"
                  placeholder="note…"
                  value={mark.note}
                  onChange={(event) => updateMarkNote(mark.id, event.target.value)}
                />
              </li>
            ))}
          </ul>
        )}
        {sendStatus && (
          <div className="panel-status" role="status">
            {sendStatus}
          </div>
        )}
      </div>
      <div className="statusbar" role="status">
        {statusText}
      </div>
    </div>
  );
}
