/// <reference lib="webworker" />
import { extractGcode } from './container';
import { parseAndBuild } from './buildGeometry';
import type { ParseRequest, ParseResponse } from '../types';

// Runs the (potentially ~14 MB) unzip + parse + buffer build off the main thread so the UI
// stays responsive. Result buffers are transferred (zero-copy) back to the main thread.

const ctx = self as unknown as DedicatedWorkerGlobalScope;

ctx.onmessage = (e: MessageEvent<ParseRequest>) => {
  const { name, buffer } = e.data;
  try {
    const { gcode } = extractGcode(name, buffer);
    const result = parseAndBuild(gcode);

    const transfer: Transferable[] = [];
    for (const t of Object.values(result.byType)) {
      if (t) transfer.push(t.positions.buffer, t.layerStart.buffer);
    }
    transfer.push(result.retractions.positions.buffer, result.retractions.layerStart.buffer);

    const res: ParseResponse = { ok: true, result };
    ctx.postMessage(res, transfer);
  } catch (err) {
    const res: ParseResponse = {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
    ctx.postMessage(res);
  }
};
