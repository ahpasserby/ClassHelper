import { resolve } from "node:path";
import { defineConfig, externalizeDepsPlugin } from "electron-vite";
import react from "@vitejs/plugin-react";

/**
 * The renderer lives in ../web and is built into the Python package.
 *
 * The window loads the server's URL, not a file:// bundle, so the server is
 * what serves the UI -- which keeps every fetch a same-origin relative path and
 * means no CORS, no origin threaded through the preload, and one place the
 * assets can be. Building anywhere else would just produce output nothing
 * loads.
 */
export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: { rollupOptions: { input: resolve(__dirname, "src/main/index.ts") } },
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: { rollupOptions: { input: resolve(__dirname, "src/preload/index.ts") } },
  },
  renderer: {
    root: resolve(__dirname, "../web"),
    plugins: [react()],
    build: {
      outDir: resolve(__dirname, "../src/classhelper/web"),
      emptyOutDir: true,
      // electron-vite leaves the renderer unminified; this is a shipped app,
      // and the difference was 2.4 MB against 1.1 MB.
      minify: true,
      rollupOptions: { input: resolve(__dirname, "../web/index.html") },
    },
  },
});
