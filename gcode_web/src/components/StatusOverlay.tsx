import { useViewerStore } from '../state/viewerStore';

export function StatusOverlay() {
  const status = useViewerStore((s) => s.status);
  const error = useViewerStore((s) => s.error);
  // Derive (don't select) the array — a `?? []` inside the selector returns a new
  // reference every call and sends useSyncExternalStore into an infinite render loop.
  const result = useViewerStore((s) => s.result);
  const warnings = result?.warnings ?? [];

  if (status === 'idle') {
    return (
      <div className="overlay center">
        <div className="dropcard">
          <div className="dropcard-title">Drop a G-code file</div>
          <div className="dropcard-sub">
            .gcode or Bambu/Orca .gcode.3mf — drag it anywhere, or use Open file…
          </div>
        </div>
      </div>
    );
  }

  if (status === 'parsing') {
    return (
      <div className="overlay center">
        <div className="spinner" />
        <div className="overlay-text">Parsing…</div>
      </div>
    );
  }

  if (status === 'error') {
    return (
      <div className="overlay center">
        <div className="errorcard">
          <div className="errorcard-title">Couldn't load that file</div>
          <div className="errorcard-msg">{error}</div>
        </div>
      </div>
    );
  }

  // ready — surface non-fatal warnings as a small toast
  if (warnings.length > 0) {
    return (
      <div className="overlay toast">
        {warnings.map((w, i) => (
          <div key={i} className="toast-line">
            ⚠ {w}
          </div>
        ))}
      </div>
    );
  }

  return null;
}
