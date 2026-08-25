import { readFileSync } from "node:fs";
import { defineConfig } from "vite";

const pkg = JSON.parse(readFileSync("./package.json", "utf8"));

export default defineConfig({
  define: { __SS_VERSION__: JSON.stringify(pkg.version) },
  build: {
    target: "es2022",
    minify: "esbuild",
    sourcemap: true,
    // Served by the integration itself, so the artifact lands inside the
    // component directory and ships in the same release zip as the Python.
    outDir: "custom_components/solar_sanity/frontend",
    emptyOutDir: false,
    lib: {
      entry: "frontend/src/main.ts",
      formats: ["es"],
      fileName: () => "solar-sanity.js",
    },
    rollupOptions: {
      // The key that actually guarantees a single file. `codeSplitting: false`
      // is widely copied from other card repos and is a silent no-op.
      output: { inlineDynamicImports: true },
    },
  },
  preview: { port: 4000, host: "0.0.0.0", cors: true },
});
