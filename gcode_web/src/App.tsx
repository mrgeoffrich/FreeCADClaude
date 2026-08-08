import { useState } from 'react';
import { useGcodeFile } from './hooks/useGcodeFile';
import { Viewer } from './components/Viewer';
import { FileDrop } from './components/FileDrop';
import { Legend } from './components/Legend';
import { LayerRangeSlider } from './components/LayerRangeSlider';
import { StatusOverlay } from './components/StatusOverlay';
import { SettingsDrawer } from './components/SettingsDrawer';

export function App() {
  const { loadFile } = useGcodeFile();
  const [dragging, setDragging] = useState(false);
  // Held here rather than in the store: the toolbar opens it and the drawer
  // closes it, and nothing else in the app has an opinion about it. The drawer
  // is mounted only while open, so each opening starts from what is stored.
  const [settingsOpen, setSettingsOpen] = useState(false);

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
