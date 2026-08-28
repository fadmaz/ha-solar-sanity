/**
 * The built file, not the source.
 *
 * Every other test here imports TypeScript modules and never touches the
 * artifact users actually load. That gap is real: upgrading to Vite 8 swapped
 * the bundler for Rolldown and the minifier for Oxc, and nothing in the suite
 * would have noticed if the output had stopped registering a custom element or
 * had thrown on evaluation. The source was unchanged and every source test
 * passed.
 *
 * So this loads `solar-sanity.js` the way a browser would, and asserts the few
 * things the integration depends on: that it evaluates, that it defines what it
 * claims to, and that a card renders its degraded state with no Home Assistant.
 *
 * Reached through `import.meta.glob` rather than `node:fs` on purpose. This
 * package deliberately has no `@types/node` — the tsconfig says so, and card
 * source has no business reaching for Node APIs. The glob is resolved by Vite,
 * returns nothing when there is no build, and needs no types at all.
 */

import { describe, expect, it } from "vitest";

const BUNDLE = "../../custom_components/solar_sanity/frontend/solar-sanity.js";

const modules = import.meta.glob<{ default: unknown }>(
  "../../custom_components/solar_sanity/frontend/solar-sanity.js",
);
const sources = import.meta.glob<string>(
  "../../custom_components/solar_sanity/frontend/solar-sanity.js",
  { query: "?raw", import: "default" },
);

const key = Object.keys(modules)[0];
const built = key !== undefined;

/** Evaluate the bundle once; every case here needs it already registered. */
async function load(): Promise<void> {
  await modules[key as string]!();
}

async function text(): Promise<string> {
  return sources[Object.keys(sources)[0] as string]!();
}

describe.skipIf(!built)(`the built bundle (${BUNDLE})`, () => {
  it("evaluates, and defines everything it promises", async () => {
    await load();

    for (const tag of [
      "solar-sanity-card",
      "solar-sanity-forecast-card",
      "ll-strategy-view-solar-sanity",
      "ll-strategy-dashboard-solar-sanity",
    ]) {
      expect(customElements.get(tag), `${tag} was not registered`).toBeTruthy();
    }
  });

  it("registers both cards in the picker", async () => {
    await load();
    const cards = (window as unknown as { customCards?: { type: string }[] }).customCards;

    // Bare, with no `custom:` prefix. That prefix belongs in a dashboard's
    // config; the picker registry wants the element name as defined.
    expect(cards?.map((c) => c.type).sort()).toEqual([
      "solar-sanity-card",
      "solar-sanity-forecast-card",
    ]);
  });

  it("offers the dashboard strategy by name", async () => {
    await load();
    const strategies = (window as unknown as { customStrategies?: { type: string }[] })
      .customStrategies;

    expect(strategies?.some((s) => s.type === "solar-sanity")).toBe(true);
  });

  it("renders a sentence rather than nothing when Home Assistant is absent", async () => {
    await load();

    const card = document.createElement("solar-sanity-card") as HTMLElement & {
      setConfig: (config: unknown) => void;
      updateComplete: Promise<unknown>;
    };
    card.setConfig({ type: "custom:solar-sanity-card" });
    document.body.appendChild(card);
    await card.updateComplete;

    // The degraded state is the one a user meets first and the one a minifier
    // is likeliest to have broken, because nothing else exercises it.
    expect(card.shadowRoot?.textContent?.trim()).toBeTruthy();
  });

  it("is one file with nothing left to fetch", async () => {
    const source = await text();

    // A dynamic import or a bare external specifier would mean the integration
    // has to serve a second file it does not know about. The card ships as a
    // single static asset, so either would 404 in the field.
    expect(source).not.toMatch(/\bimport\s*\(/);
    expect(source).not.toMatch(/from\s*["']https?:/);
  });

  it("carries a version stamp rather than the placeholder", async () => {
    const source = await text();

    expect(source).not.toContain("__SS_VERSION__");
    expect(source).toMatch(/\d+\.\d+\.\d+/);
  });
});
