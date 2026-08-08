import { FEATURE_TYPES } from '../types';
import { FEATURE_STYLES } from '../featureColors';
import { useViewerStore } from '../state/viewerStore';
import { PREDICTION_COLOR } from './PredictionOverlay';
import { DEVIATION_GRADIENT } from '../deviationColor';

function DeviationBar({ scale }: { scale: number }) {
  return (
    <div className="devbar">
      <div className="devbar-grad" />
      <div className="devbar-labels">
        <span>−{scale.toFixed(2)}</span>
        <span>0</span>
        <span>+{scale.toFixed(2)}</span>
      </div>
      <div className="devbar-cap">undersized · deviation mm · oversized</div>
    </div>
  );
}

export function Legend() {
  const result = useViewerStore((s) => s.result);
  const visible = useViewerStore((s) => s.visible);
  const toggleType = useViewerStore((s) => s.toggleType);
  const setVisibleAll = useViewerStore((s) => s.setVisibleAll);
  const showGcode = useViewerStore((s) => s.showGcode);
  const toggleGcode = useViewerStore((s) => s.toggleGcode);
  const showStl = useViewerStore((s) => s.showStl);
  const toggleStl = useViewerStore((s) => s.toggleStl);
  const showRetractions = useViewerStore((s) => s.showRetractions);
  const toggleRetractions = useViewerStore((s) => s.toggleRetractions);
  const travelTopLayerOnly = useViewerStore((s) => s.travelTopLayerOnly);
  const toggleTravelTopLayerOnly = useViewerStore((s) => s.toggleTravelTopLayerOnly);
  const prediction = useViewerStore((s) => s.prediction);
  const showPrediction = useViewerStore((s) => s.showPrediction);
  const togglePrediction = useViewerStore((s) => s.togglePrediction);
  const mesh = useViewerStore((s) => s.mesh);
  const showMesh = useViewerStore((s) => s.showMesh);
  const toggleMesh = useViewerStore((s) => s.toggleMesh);
  const layerRange = useViewerStore((s) => s.layerRange);

  if (!result) return null;

  const present = FEATURE_TYPES.filter((t) => (result.stats.segmentsByType[t] ?? 0) > 0);
  const unknown = result.stats.unknownFeatureStrings;

  // Retractions within the currently visible layer band (mirrors the draw-range math).
  const rt = result.retractions;
  const rtLo = Math.max(0, Math.min(layerRange[0], result.layerCount));
  const rtHi = Math.max(rtLo, Math.min(layerRange[1] + 1, result.layerCount));
  const retractionsInView = rt.layerStart[rtHi] - rt.layerStart[rtLo];

  return (
    <div className="panel legend">
      <div className="panel-title">
        <label className="panel-master">
          <input type="checkbox" checked={showGcode} onChange={toggleGcode} />
          <span>G-code</span>
        </label>
        <span className="panel-actions">
          <button onClick={() => setVisibleAll(true)}>All</button>
          <button onClick={() => setVisibleAll(false)}>None</button>
        </span>
      </div>

      <ul className={`legend-list${showGcode ? '' : ' dimmed'}`}>
        {present.map((t) => {
          const style = FEATURE_STYLES[t];
          const count = result.stats.segmentsByType[t] ?? 0;
          return (
            <li key={t}>
              <label className="legend-row">
                <input
                  type="checkbox"
                  checked={!!visible[t]}
                  onChange={() => toggleType(t)}
                />
                <span className="swatch" style={{ background: style.color }} />
                <span className="legend-label">{style.label}</span>
                <span className="legend-count">{count.toLocaleString()}</span>
              </label>
              {t === 'travel' && visible[t] && (
                <label className="legend-subrow">
                  <input
                    type="checkbox"
                    checked={travelTopLayerOnly}
                    onChange={toggleTravelTopLayerOnly}
                  />
                  Top layer only
                </label>
              )}
            </li>
          );
        })}
      </ul>

      {(result.retractions.count > 0 || prediction || mesh) && (
        <div className="legend-markers">
          {result.retractions.count > 0 && (
            <label className="legend-row">
              <input
                type="checkbox"
                checked={showRetractions}
                onChange={toggleRetractions}
              />
              <span className="swatch dot" style={{ background: '#ffffff' }} />
              <span className="legend-label">Retractions</span>
              <span className="legend-count" title="in view / total">
                {retractionsInView.toLocaleString()} / {rt.count.toLocaleString()}
              </span>
            </label>
          )}
          {prediction && (
            <>
              <label className="legend-row">
                <input
                  type="checkbox"
                  checked={showPrediction}
                  onChange={togglePrediction}
                />
                <span
                  className="swatch"
                  style={{ background: prediction.hasDeviation ? DEVIATION_GRADIENT : PREDICTION_COLOR }}
                />
                <span className="legend-label">Predicted edge</span>
                <span className="legend-count">{prediction.count.toLocaleString()}</span>
              </label>
              {prediction.hasDeviation && <DeviationBar scale={prediction.devScale} />}
            </>
          )}

          {mesh && (
            <>
              <label className="legend-row">
                <input type="checkbox" checked={showMesh} onChange={toggleMesh} />
                <span className="swatch" style={{ background: DEVIATION_GRADIENT }} />
                <span className="legend-label">Predicted mesh (vs STL)</span>
                <span className="legend-count">{mesh.faceCount.toLocaleString()}△</span>
              </label>
              <DeviationBar scale={mesh.devScale} />
            </>
          )}

          {mesh && mesh.stlIndices.length > 0 && (
            <label className="legend-row">
              <input type="checkbox" checked={showStl} onChange={toggleStl} />
              <span className="swatch" style={{ background: '#aab4c4' }} />
              <span className="legend-label">STL (reference)</span>
            </label>
          )}
        </div>
      )}

      {unknown.length > 0 && (
        <div className="legend-unknown" title={unknown.join(', ')}>
          Unmapped: {unknown.join(', ')}
        </div>
      )}
    </div>
  );
}
