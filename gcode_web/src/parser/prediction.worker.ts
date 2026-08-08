/// <reference lib="webworker" />
import { buildPrediction } from './prediction';
import type { PredictionRequest, PredictionResponse } from '../types';

const ctx = self as unknown as DedicatedWorkerGlobalScope;

ctx.onmessage = (e: MessageEvent<PredictionRequest>) => {
  try {
    const prediction = buildPrediction(e.data.buffer);
    const res: PredictionResponse = { ok: true, prediction };
    ctx.postMessage(res, [prediction.positions.buffer, prediction.layerStart.buffer]);
  } catch (err) {
    const res: PredictionResponse = {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
    ctx.postMessage(res);
  }
};
