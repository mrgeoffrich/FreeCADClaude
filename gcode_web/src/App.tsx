import { useEffect, useState } from 'react';
import { gcodeIdFromSearch, useGcodeFile } from './hooks/useGcodeFile';
import { Viewer } from './components/Viewer';
import { FileDrop } from './components/FileDrop';
import { Legend } from './components/Legend';
import { LayerRangeSlider } from './components/LayerRangeSlider';
import { StatusOverlay } from './components/StatusOverlay';
import { SettingsDrawer } from './components/SettingsDrawer';

export function App() {
  const { loadFile, loadUrl } = useGcodeFile();
  const [dragging, setDragging] = useState(false);
  // Held here rather than in the store: the toolbar opens it and the drawer
  // closes it, and nothing else in the app has an opinion about it. The drawer
  // is mounted only while open, so each opening starts from what is stored.
  const [settingsOpen, setSettingsOpen] = useState(false);

  // Local patch (FreeCADClaude) -- see VENDORED.md. view_gcode opens this page
  // as `?gcode=<id>`, naming a file the server has published. Runs once, after
  // useGcodeFile's own effect has created the workers.
  useEffect(() => {
    const id = gcodeIdFromSearch(window.location.search);
    if (id) loadUrl(`/api/gcode/${encodeURIComponent(id)}`, `${id}.gcode`);
  }, [loadUrl]);

  return (
    <div
      className={`app${dragging ? ' dragging' : ''}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => {
        // Only clear when the cursor leaves the window, not when crossing child elements.
        if (e.relatedTarget === null) setDragging(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        const f = e.dataTransfer.files?.[0];
        if (f) loadFile(f);
      }}
    >
      <Viewer />
      <FileDrop onFile={loadFile} onSettings={() => setSettingsOpen((was) => !was)} />
      <Legend />
      <LayerRangeSlider />
      <StatusOverlay />
      {settingsOpen && <SettingsDrawer onClose={() => setSettingsOpen(false)} />}
      {dragging && <div className="drag-veil">Drop to load</div>}
    </div>
  );
}
