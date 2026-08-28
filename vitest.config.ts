import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

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
  // Anchored to this file so `include` below means the same thing from the
  // repository root, from `frontend/`, or from an editor's test runner. It
  // defaults to the working directory, which silently found no test files at
  // all rather than reporting a path problem.
  root: fileURLToPath(new URL(".", import.meta.url)),
  // The same injection the build does. Without it a card under test throws on
  // an undefined global rather than on whatever it is being tested for.
  define: { __SS_VERSION__: JSON.stringify(manifest.version) },
  test: {
    // Lit needs a DOM even to define a component, and the cards are the point.
    // The pure modules do not care, and pay only the startup.
    environment: "happy-dom",
    include: ["frontend/src/**/*.test.ts"],
    // Beside the code they test, not in a parallel tree. There is no
    // integration layer to keep separate here — every one of these is a unit
    // test of a function or a single component.
    restoreMocks: true,
  },
});
