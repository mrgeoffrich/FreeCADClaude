import { useCallback, useEffect, useRef } from 'react';
import type { ParseResponse, PredictionResponse, MeshResponse } from '../types';
import { useViewerStore } from '../state/viewerStore';

/** Predicted-MESH file (deviation vs STL): `*.mesh.json[.gz]`. */
export function isMeshFile(name: string): boolean {
  return /\.mesh\.json(\.gz)?$/i.test(name);
}

/** Predicted-EDGE GeoJSON file. */
export function isPredictionFile(name: string): boolean {
  return /\.geojson(\.gz)?$|\.json$/i.test(name);
}

/**
 * Owns the parser, prediction and mesh Web Workers and wires their results into the store.
 */
export function useGcodeFile() {
  const gcodeWorker = useRef<Worker | null>(null);
  const predWorker = useRef<Worker | null>(null);
  const meshWorker = useRef<Worker | null>(null);
  const gcodeName = useRef('');
  const predName = useRef('');
  const meshName = useRef('');

  useEffect(() => {
    const gw = new Worker(new URL('../parser/parser.worker.ts', import.meta.url), { type: 'module' });
    gw.onmessage = (e: MessageEvent<ParseResponse>) => {
      const store = useViewerStore.getState();
      if (e.data.ok) store.setResult(gcodeName.current, e.data.result);
      else store.setError(e.data.error);
    };
    gw.onerror = (e) => useViewerStore.getState().setError(e.message || 'Parser worker crashed.');
    gcodeWorker.current = gw;

    const pw = new Worker(new URL('../parser/prediction.worker.ts', import.meta.url), { type: 'module' });
    pw.onmessage = (e: MessageEvent<PredictionResponse>) => {
      const store = useViewerStore.getState();
      if (e.data.ok) store.setPrediction(predName.current, e.data.prediction);
      else store.setError(e.data.error);
    };
    pw.onerror = (e) => useViewerStore.getState().setError(e.message || 'Prediction worker crashed.');
    predWorker.current = pw;

    const mw = new Worker(new URL('../parser/mesh.worker.ts', import.meta.url), { type: 'module' });
    mw.onmessage = (e: MessageEvent<MeshResponse>) => {
      const store = useViewerStore.getState();
      if (e.data.ok) store.setMesh(meshName.current, e.data.mesh);
      else store.setError(e.data.error);
    };
    mw.onerror = (e) => useViewerStore.getState().setError(e.message || 'Mesh worker crashed.');
    meshWorker.current = mw;

    return () => {
      gw.terminate();
      pw.terminate();
      mw.terminate();
      gcodeWorker.current = predWorker.current = meshWorker.current = null;
    };
  }, []);

  const parseGcode = useCallback((name: string, buffer: ArrayBuffer) => {
    gcodeName.current = name;
    useViewerStore.getState().beginParse(name);
    gcodeWorker.current?.postMessage({ name, buffer }, [buffer]);
  }, []);

  const parsePrediction = useCallback((name: string, buffer: ArrayBuffer) => {
    predName.current = name;
    predWorker.current?.postMessage({ buffer }, [buffer]);
  }, []);

  const parseMesh = useCallback((name: string, buffer: ArrayBuffer) => {
    meshName.current = name;
    meshWorker.current?.postMessage({ buffer }, [buffer]);
  }, []);

  const loadFile = useCallback(
    async (file: File) => {
      try {
        const buffer = await file.arrayBuffer();
        if (isMeshFile(file.name)) parseMesh(file.name, buffer);
        else if (isPredictionFile(file.name)) parsePrediction(file.name, buffer);
        else parseGcode(file.name, buffer);
      } catch (err) {
        useViewerStore.getState().setError(err instanceof Error ? err.message : String(err));
      }
    },
    [parseGcode, parsePrediction, parseMesh],
  );

  return { loadFile };
}
