/// <reference types="vitest/config" />
// SPDX-License-Identifier: LGPL-2.1-or-later
// Build config for the face-markup 3D model viewer (model_web/ -> Vite ->
// freecad/freecadclaude/model_ui/). Mirrors gcode_web/vite.config.ts exactly,
// adjusted only for the different output directory and the single Web Worker
// (this app's occt-import-js parse worker, versus dimensioner's three).
//
// The output directory is inside the Python package and is committed to git:
// users install FreeCADClaude from the `main` branch via the Addon Manager,
// which is a plain file copy with no Node, no npm and no build step. If
// model_ui/ isn't in the tree, the feature doesn't exist for them.
//
// Everything below exists to keep that committed tree small and reviewable --
// a handful of stable filenames rather than a hashed module graph whose every
// rebuild churns the diff:
//   base "./"              -- the page is served from the server root, but
//                             relative URLs also make the built tree openable
//                             for debugging.
//   inlineDynamicImports   -- one JS file, no code splitting.
//   assetsInlineLimit      -- fonts/images/etc. land as data URIs in that file.
//   cssCodeSplit false     -- one CSS file.
//   fixed *FileNames       -- no content hashes, so a rebuild that changes
//                             nothing produces no diff at all.
//
// The Web Worker is a separate Rollup entry point that Vite builds in its own
// `worker` pass, so it needs its own fixed name. It is kept because
// occt-import-js's parse+tessellate pass is real CPU work, and it would
// otherwise contend with orbiting the loaded model on the UI thread.
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: './',
  plugins: [react()],
  build: {
    outDir: '../freecad/freecadclaude/model_ui',
    emptyOutDir: true,
    target: 'es2022',
    // 1 MB: well above anything this app inlines, so no asset ever becomes a
    // separate file we'd have to serve and commit -- except the occt-import-js
    // WASM kernel (~7.6 MB), which deliberately lands as its own file below.
    assetsInlineLimit: 1024 * 1024,
    cssCodeSplit: false,
    // The polyfill is a separate injected chunk; a single-file build has
    // nothing to preload.
    modulePreload: { polyfill: false },
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
        entryFileNames: 'assets/app.js',
        chunkFileNames: 'assets/app.js',
        assetFileNames: 'assets/app.[ext]',
      },
    },
  },
  worker: {
    format: 'es',
    rollupOptions: {
      output: {
        entryFileNames: 'assets/[name].js',
        chunkFileNames: 'assets/[name].js',
        // The worker's assets -- the occt-import-js WASM kernel above all --
        // are emitted by this pass, not the client one, so they need the same
        // fixed-name treatment here or a rebuild that changes nothing would
        // churn a content hash in the committed output.
        assetFileNames: 'assets/app.[ext]',
      },
    },
  },
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
  },
});
