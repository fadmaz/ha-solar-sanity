import { readFileSync } from "node:fs";
import { defineConfig } from "vitest/config";

const manifest = JSON.parse(
  readFileSync("./custom_components/solar_sanity/manifest.json", "utf8"),
);

export default defineConfig({
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
