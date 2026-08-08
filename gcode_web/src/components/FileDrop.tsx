import { useRef } from 'react';
import { useViewerStore } from '../state/viewerStore';

interface FileDropProps {
  onFile: (file: File) => void;
  onSettings: () => void;
}

export function FileDrop({ onFile, onSettings }: FileDropProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const fileName = useViewerStore((s) => s.fileName);
  const status = useViewerStore((s) => s.status);
  const projection = useViewerStore((s) => s.projection);
  const toggleProjection = useViewerStore((s) => s.toggleProjection);

  return (
    <header className="toolbar">
      <div className="brand">
        Dimensioner <span>· G-code viewer</span>
      </div>
      <div className="toolbar-spacer" />
      {fileName && status === 'ready' && <div className="toolbar-file">{fileName}</div>}
      <div className="toolbar-actions">
        <button
          className="secondary"
          onClick={toggleProjection}
          title="Toggle perspective / isometric projection"
        >
          {projection === 'isometric' ? '◳ Isometric' : '◰ Perspective'}
        </button>
        <button
          className="secondary"
          onClick={onSettings}
          title="Which printer, nozzle, process and filament the addon slices with"
        >
          ⚙ Slicer settings
        </button>
        <button onClick={() => inputRef.current?.click()}>Open file…</button>
        <input
          ref={inputRef}
          type="file"
          accept=".gcode,.gco,.3mf,.zip,.geojson,.json,.gz"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onFile(f);
            e.target.value = '';
          }}
        />
      </div>
    </header>
  );
}
