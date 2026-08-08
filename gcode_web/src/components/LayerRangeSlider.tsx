import { useEffect, useRef, useState } from 'react';
import { useViewerStore } from '../state/viewerStore';

type Thumb = 'min' | 'max';

// Vertical, dual-thumb layer range. Top of the track = top of the print (highest layer),
// bottom = the build plate. Each thumb maps a layer index to a vertical position.
export function LayerRangeSlider() {
  const result = useViewerStore((s) => s.result);
  const layerRange = useViewerStore((s) => s.layerRange);
  const setLayerRange = useViewerStore((s) => s.setLayerRange);

  const trackRef = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState<Thumb | null>(null);

  useEffect(() => {
    if (!active) return;

    const layerFromClientY = (clientY: number): number => {
      const el = trackRef.current;
      const max = Math.max((useViewerStore.getState().result?.layerCount ?? 1) - 1, 0);
      if (!el) return 0;
      const rect = el.getBoundingClientRect();
      const frac = 1 - (clientY - rect.top) / rect.height; // top = 1, bottom = 0
      return Math.max(0, Math.min(max, Math.round(frac * max)));
    };

    const onMove = (e: PointerEvent) => {
      const [lo, hi] = useViewerStore.getState().layerRange;
      const l = layerFromClientY(e.clientY);
      if (active === 'min') setLayerRange([Math.min(l, hi), hi]);
      else setLayerRange([lo, Math.max(l, lo)]);
    };
    const onUp = () => setActive(null);

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
  }, [active, setLayerRange]);

  if (!result) return null;

  const maxLayer = Math.max(result.layerCount - 1, 0);
  const denom = maxLayer || 1;
  const [lo, hi] = layerRange;

  const topPct = (l: number) => (1 - l / denom) * 100;
  const zText = (l: number) => {
    const z = result.layerZ[l];
    return Number.isFinite(z) ? `${z.toFixed(2)} mm` : '';
  };

  return (
    <div className="panel slider-panel">
      <div className="panel-title">Layers</div>

      <div className="slider-readout">
        <div className="slider-num">{hi}</div>
        <div className="slider-z">{zText(hi)}</div>
      </div>

      <div
        className="slider-track"
        ref={trackRef}
        onPointerDown={(e) => {
          // Click on the track jumps the nearest thumb.
          const rect = trackRef.current!.getBoundingClientRect();
          const frac = 1 - (e.clientY - rect.top) / rect.height;
          const l = Math.max(0, Math.min(maxLayer, Math.round(frac * maxLayer)));
          const which: Thumb = Math.abs(l - hi) <= Math.abs(l - lo) ? 'max' : 'min';
          if (which === 'max') setLayerRange([lo, Math.max(l, lo)]);
          else setLayerRange([Math.min(l, hi), hi]);
          setActive(which);
        }}
      >
        <div
          className="slider-fill"
          style={{ top: `${topPct(hi)}%`, bottom: `${(lo / denom) * 100}%` }}
        />
        <div
          className="slider-thumb"
          role="slider"
          aria-label="Top layer"
          aria-valuemin={0}
          aria-valuemax={maxLayer}
          aria-valuenow={hi}
          tabIndex={0}
          style={{ top: `${topPct(hi)}%` }}
          onPointerDown={(e) => {
            e.stopPropagation();
            setActive('max');
          }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowUp') setLayerRange([lo, Math.min(maxLayer, hi + 1)]);
            if (e.key === 'ArrowDown') setLayerRange([lo, Math.max(lo, hi - 1)]);
          }}
        />
        <div
          className="slider-thumb"
          role="slider"
          aria-label="Bottom layer"
          aria-valuemin={0}
          aria-valuemax={maxLayer}
          aria-valuenow={lo}
          tabIndex={0}
          style={{ top: `${topPct(lo)}%` }}
          onPointerDown={(e) => {
            e.stopPropagation();
            setActive('min');
          }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowUp') setLayerRange([Math.min(hi, lo + 1), hi]);
            if (e.key === 'ArrowDown') setLayerRange([Math.max(0, lo - 1), hi]);
          }}
        />
      </div>

      <div className="slider-readout">
        <div className="slider-num">{lo}</div>
        <div className="slider-z">{zText(lo)}</div>
      </div>

      <div className="slider-total">of {maxLayer}</div>
    </div>
  );
}
