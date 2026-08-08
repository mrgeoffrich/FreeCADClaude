/// <reference types="vitest/config" />
// SPDX-License-Identifier: LGPL-2.1-or-later
// Build config for the vendored G-code viewer. Replaces upstream dimensioner's
// own vite.config.ts; see VENDORED.md.
//
// The output directory is inside the Python package and is committed to git:
// users install FreeCADClaude from the `main` branch via the Addon Manager,
// which is a plain file copy with no Node, no npm and no build step. If
// gcode_ui/ isn't in the tree, the feature doesn't exist for them.
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
// The three Web Workers are separate Rollup entry points that Vite builds in
// its own `worker` pass, so they need their own fixed names. They are kept
// because a large G-code parse would otherwise block the UI thread, and they
// transfer typed buffers zero-copy.
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: './',
  plugins: [react()],
  build: {
    outDir: '../freecad/freecadclaude/gcode_ui',
    emptyOutDir: true,
    target: 'es2022',
    // 1 MB: well above anything this app inlines, so no asset ever becomes a
    // separate file we'd have to serve and commit.
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
      },
    },
  },
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
  },
});
