import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const manifest = JSON.parse(
  // Resolved against this file, not the shell's working directory. A relative
  // path here means the config only loads from the repository root, and the
  // failure is an ENOENT during startup rather than anything that names cwd.
  readFileSync(
    fileURLToPath(new URL("./custom_components/solar_sanity/manifest.json", import.meta.url)),
    "utf8",
  ),
);

export default defineConfig({
  // From the integration's manifest, not package.json. The two drifted to
  // 0.1.0 and 0.7.0, so the card reported a version six minors stale — and
  // anything built on comparing them would have been broken from the start.
  define: { __SS_VERSION__: JSON.stringify(manifest.version) },
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
