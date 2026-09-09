import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build lands inside the Python package, so `pip install classhelper` ships
// a working reader and end users never need Node installed. Only contributors
// touching the UI run this.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/classhelper/web",
    emptyOutDir: true,
    // The built bundle is committed so that installing the package is enough
    // to run the reader. A source map would be four megabytes of that, in
    // every clone and every release -- contributors build from source anyway.
    sourcemap: false,
  },
  server: {
    port: 5173,
    // In development the UI runs under Vite and the API under uvicorn.
    proxy: { "/api": { target: "http://127.0.0.1:8799", changeOrigin: true } },
  },
});
