/// SPDX-License-Identifier: LGPL-2.1-or-later
// Build config for the device-annotation web app.
//
// The output directory is INSIDE the Python package and is committed to git:
// users install FreeCADClaude from the `main` branch via the Addon Manager,
// which is a plain file copy with no Node, no npm and no build step. If
// device_ui/ isn't in the tree, the feature doesn't exist for them.
//
// Everything below exists to keep that committed tree small and reviewable --
// a handful of stable filenames rather than a hashed module graph whose every
// rebuild churns the diff:
//   base "./"              -- the page is served from the server root, but
//                             relative URLs also make the built tree openable
//                             straight off disk when debugging.
//   inlineDynamicImports   -- one JS file, no code splitting.
//   assetsInlineLimit      -- fonts/images/etc. land as data URIs in that file.
//   cssCodeSplit false     -- one CSS file.
//   fixed *FileNames       -- no content hashes, so a rebuild that changes
//                             nothing produces no diff at all.
import { defineConfig } from "vitest/config";

export default defineConfig({
  base: "./",
  build: {
    outDir: "../freecad/freecadclaude/device_ui",
    emptyOutDir: true,
    target: "es2022",
    // 1 MB: well above anything this app will ever inline, so no asset ever
    // becomes a separate file we'd have to serve and commit.
    assetsInlineLimit: 1024 * 1024,
    cssCodeSplit: false,
    // The polyfill is a separate injected chunk; a single-file build has
    // nothing to preload.
    modulePreload: { polyfill: false },
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
        entryFileNames: "assets/app.js",
        chunkFileNames: "assets/app.js",
        assetFileNames: "assets/app.[ext]",
      },
    },
  },
  test: {
    environment: "node",
    include: ["test/**/*.test.ts"],
  },
});
