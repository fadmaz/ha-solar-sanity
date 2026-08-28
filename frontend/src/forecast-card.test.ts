import { beforeEach, describe, expect, it, vi } from "vitest";

import { buildPanel, SolarSanityForecastCard } from "./forecast-card";
import { DAYAHEAD_PREFIX, type ProviderDay } from "./forecast-data";
import type { HomeAssistant } from "./types/hass";

const ID = `${DAYAHEAD_PREFIX}01ABC`;

function day(kwh: number[], label = "Forecast.Solar"): ProviderDay {
  return {
    statisticId: ID,
    label,
    hours: kwh.map((value, index) => ({
      start: new Date(2026, 7, 29, index),
      kwh: value,
    })),
  };
}

describe("buildPanel", () => {
  it("draws the shape, and the shape is the contract", () => {
    // The `d` attribute is the drawing. Snapshotting it is the one rendering
    // test worth having: short, readable in a diff, and it fails on a change
    // to the geometry rather than to the markup around it.
    const panel = buildPanel(day([0, 1, 2, 1]));

    expect(panel.line).toBe("M34,158 L220,84 L406,10 L592,84");
  });

  it("closes the area down to the axis, not to the top", () => {
    const panel = buildPanel(day([0, 1]));

    expect(panel.area.endsWith("L592,158 L34,158 Z")).toBe(true);
  });

  it("totals the day", () => {
    expect(buildPanel(day([0, 1.25, 2.5])).kwh).toBeCloseTo(3.75);
  });

  it("scales to the day, so a dull day is not a flat line at the floor", () => {
    const dull = buildPanel(day([0, 0.4, 0.8]));
    const bright = buildPanel(day([0, 4, 8]));

    // Both peak at the same height: each day is drawn against its own axis.
    expect(dull.line.split(" ").at(-1)).toBe(bright.line.split(" ").at(-1));
  });

  it("gives an axis that contains the data", () => {
    const panel = buildPanel(day([0, 3.7]));

    expect(panel.ticks.at(-1)!).toBeGreaterThanOrEqual(3.7);
  });

  it("survives a day with a single hour in it", () => {
    const panel = buildPanel(day([1]));

    expect(panel.line).toBe("");
    expect(panel.kwh).toBe(1);
  });

  it("survives an empty day", () => {
    const panel = buildPanel(day([]));

    expect(panel.line).toBe("");
    expect(panel.area).toBe("");
    expect(panel.kwh).toBe(0);
  });

  it("labels hours from their own timestamps", () => {
    const panel = buildPanel(day([0, 0, 0, 0, 0, 0, 0]));

    expect(panel.labels[0]!.text).toBe("00");
    expect(panel.labels[6]!.text).toBe("06");
  });
});

describe("the card", () => {
  const hass = (overrides: Partial<HomeAssistant> = {}) =>
    ({
      states: {},
      config: { state: "RUNNING" },
      language: "en",
      callWS: vi.fn(() => Promise.resolve([])),
      ...overrides,
    }) as unknown as HomeAssistant;

  let card: SolarSanityForecastCard;

  beforeEach(() => {
    card = document.createElement("solar-sanity-forecast-card") as SolarSanityForecastCard;
    card.setConfig({ type: "custom:solar-sanity-forecast-card" });
    document.body.append(card);
  });

  const text = async () => {
    await card.updateComplete;
    // One more turn: the card fetches on the first hass update.
    await new Promise((resolve) => setTimeout(resolve, 0));
    await card.updateComplete;
    return card.shadowRoot?.textContent?.replace(/\s+/g, " ").trim() ?? "";
  };

  it("waits rather than claiming anything before Home Assistant arrives", async () => {
    expect(await text()).toContain("Waiting for Home Assistant");
  });

  it("says so while Home Assistant is still starting", async () => {
    card.hass = hass({ config: { state: "NOT_RUNNING" } } as Partial<HomeAssistant>);

    expect(await text()).toContain("still starting");
  });

  it("pitches the feature when there is no provider at all", async () => {
    // The one degraded state that is also the product's best sentence, shown
    // exactly when it is relevant.
    card.hass = hass();

    const rendered = await text();
    expect(rendered).toContain("No solar forecast to keep");
    expect(rendered).toContain("throws away");
  });

  it("distinguishes an empty archive from a missing one", async () => {
    card.hass = hass({
      callWS: vi.fn((msg: { type: string }) =>
        Promise.resolve(
          msg.type === "recorder/list_statistic_ids"
            ? [{ statistic_id: ID, name: "Forecast.Solar forecast, a day ahead" }]
            : {},
        ),
      ),
    } as unknown as Partial<HomeAssistant>);

    const rendered = await text();
    expect(rendered).toContain("This record starts today");
    expect(rendered).not.toContain("No solar forecast to keep");
  });

  it("explains itself rather than showing a red box when the recorder refuses", async () => {
    card.hass = hass({
      callWS: vi.fn(() => Promise.reject(new Error("recorder disabled"))),
    } as unknown as Partial<HomeAssistant>);

    expect(await text()).toContain("Cannot read the record");
  });

  it("draws the day once there is one", async () => {
    const rows = Array.from({ length: 6 }, (_, index) => ({
      start: new Date(2026, 7, 29, 8 + index).getTime(),
      state: index,
    }));
    card.hass = hass({
      callWS: vi.fn((msg: { type: string }) =>
        Promise.resolve(
          msg.type === "recorder/list_statistic_ids"
            ? [{ statistic_id: ID, name: "Forecast.Solar forecast, a day ahead" }]
            : { [ID]: rows },
        ),
      ),
    } as unknown as Partial<HomeAssistant>);
    await text();

    expect(card.shadowRoot?.querySelector("path.line")?.getAttribute("d")).toBeTruthy();
    expect(await text()).toContain("Forecast.Solar");
  });

  it("never claims an accuracy it has not measured", async () => {
    card.hass = hass();
    const rendered = (await text()).toLowerCase();

    for (const word of ["accurate", "accuracy", "% off", "bias", "error"]) {
      expect(rendered).not.toContain(word);
    }
  });

  it("throws only for an authoring mistake", () => {
    const fresh = document.createElement("solar-sanity-forecast-card") as SolarSanityForecastCard;

    expect(() =>
      fresh.setConfig({ type: "x", provider: 7 } as unknown as { type: string }),
    ).toThrow();
    expect(() => fresh.setConfig({ type: "x" })).not.toThrow();
  });

  it("offers a stub with nothing to fill in", () => {
    // Zero-config is the point: drop it on a dashboard and it works.
    expect(SolarSanityForecastCard.getStubConfig()).toEqual({
      type: "custom:solar-sanity-forecast-card",
    });
  });
});
