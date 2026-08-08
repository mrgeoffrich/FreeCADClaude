import { create } from 'zustand';
import type { FeatureType, MeshBuffers, ParseResult, PredictionBuffers } from '../types';
import { FEATURE_TYPES } from '../types';
import { FEATURE_STYLES } from '../featureColors';

export type Status = 'idle' | 'parsing' | 'ready' | 'error';
export type Projection = 'perspective' | 'isometric';

function defaultVisible(): Record<FeatureType, boolean> {
  const v = {} as Record<FeatureType, boolean>;
  for (const t of FEATURE_TYPES) v[t] = FEATURE_STYLES[t].defaultVisible;
  return v;
}

interface ViewerState {
  status: Status;
  fileName?: string;
  result?: ParseResult;
  error?: string;
  visible: Record<FeatureType, boolean>;
  /** Master toggle for the whole gcode toolpath (all line types + top-layer highlight). */
  showGcode: boolean;
  /** Show the registered STL as a translucent reference (carried inside the mesh export). */
  showStl: boolean;
  showRetractions: boolean;
  /** When true, travel moves render only on the top visible layer (they're very numerous). */
  travelTopLayerOnly: boolean;
  /** Predicted physical edge from the Python pipeline (GeoJSON overlay), if loaded. */
  prediction?: PredictionBuffers;
  predictionName?: string;
  showPrediction: boolean;
  mesh?: MeshBuffers;
  meshName?: string;
  showMesh: boolean;
  projection: Projection;
  /** [minLayer, maxLayer], both inclusive, 0-based. */
  layerRange: [number, number];

  beginParse(name: string): void;
  setResult(name: string, result: ParseResult): void;
  setError(message: string): void;
  toggleType(type: FeatureType): void;
  setVisibleAll(value: boolean): void;
  toggleGcode(): void;
  toggleStl(): void;
  toggleRetractions(): void;
  toggleTravelTopLayerOnly(): void;
  setPrediction(name: string, prediction: PredictionBuffers): void;
  togglePrediction(): void;
  setMesh(name: string, mesh: MeshBuffers): void;
  toggleMesh(): void;
  toggleProjection(): void;
  setLayerRange(range: [number, number]): void;
}

export const useViewerStore = create<ViewerState>((set) => ({
  status: 'idle',
  visible: defaultVisible(),
  showGcode: true,
  showStl: false,
  showRetractions: true,
  travelTopLayerOnly: true,
  showPrediction: true,
  showMesh: true,
  projection: 'isometric',
  layerRange: [0, 0],

  // Clear any prior prediction here (not in setResult) so loading a sample's gcode +
  // prediction concurrently doesn't race — whichever worker finishes last still wins.
  beginParse: (name) =>
    set({
      status: 'parsing',
      fileName: name,
      error: undefined,
      prediction: undefined,
      predictionName: undefined,
      mesh: undefined,
      meshName: undefined,
    }),

  setResult: (name, result) =>
    set({
      status: 'ready',
      fileName: name,
      result,
      error: undefined,
      visible: defaultVisible(),
      showRetractions: true,
      travelTopLayerOnly: true,
      layerRange: [0, Math.max(result.layerCount - 1, 0)],
    }),

  setError: (message) => set({ status: 'error', error: message }),

  toggleType: (type) =>
    set((s) => ({ visible: { ...s.visible, [type]: !s.visible[type] } })),

  setVisibleAll: (value) =>
    set(() => {
      const v = {} as Record<FeatureType, boolean>;
      for (const t of FEATURE_TYPES) v[t] = value;
      return { visible: v };
    }),

  toggleGcode: () => set((s) => ({ showGcode: !s.showGcode })),

  toggleStl: () => set((s) => ({ showStl: !s.showStl })),

  toggleRetractions: () => set((s) => ({ showRetractions: !s.showRetractions })),

  toggleTravelTopLayerOnly: () =>
    set((s) => ({ travelTopLayerOnly: !s.travelTopLayerOnly })),

  setPrediction: (name, prediction) =>
    set({ prediction, predictionName: name, showPrediction: true }),

  togglePrediction: () => set((s) => ({ showPrediction: !s.showPrediction })),

  setMesh: (name, mesh) => set({ mesh, meshName: name, showMesh: true }),

  toggleMesh: () => set((s) => ({ showMesh: !s.showMesh })),

  toggleProjection: () =>
    set((s) => ({ projection: s.projection === 'isometric' ? 'perspective' : 'isometric' })),

  setLayerRange: (range) => set({ layerRange: range }),
}));
