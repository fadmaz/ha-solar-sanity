import { describe, expect, it } from "vitest";

import { buildCards, SolarSanityDashboardStrategy, SolarSanityViewStrategy } from "./strategy";
import type { HomeAssistant } from "./types/hass";

const outlookOf = (device: string) => ({
  [`sensor.lowest_${device}`]: {
    entity_id: `sensor.lowest_${device}`,
    platform: "solar_sanity",
    translation_key: "battery_lowest_before_sunrise",
    device_id: device,
  },
});

const hass = (entities: Record<string, unknown> = {}) =>
  ({ states: {}, entities }) as unknown as HomeAssistant;

describe("buildCards", () => {
  it("puts the battery first and the forecast under it", () => {
    const cards = buildCards(hass(outlookOf("dev-a")));

    expect(cards.map((c) => c.type)).toEqual([
      "custom:solar-sanity-battery-card",
      "custom:solar-sanity-forecast-card",
      "custom:solar-sanity-tools-card",
    ]);
  });

  it("leaves a single battery card to find its own installation", () => {
    expect(buildCards(hass(outlookOf("dev-a")))[0]!.device_id).toBeUndefined();
  });

  it("pins each battery card to its house when there are two", () => {
    // A card that picks silently between two shows a battery the reader may
    // not be looking at.
    const cards = buildCards(hass({ ...outlookOf("dev-b"), ...outlookOf("dev-a") }));

    expect(cards.map((c) => c.device_id)).toEqual(["dev-a", "dev-b", undefined, "dev-a", "dev-b"]);
  });

  it("still lays out a view when there is no outlook to show", () => {
    // The battery card already says whether Solar Sanity is not set up or the
    // outlook is off, and how to turn it on. Writing that sentence a second
    // time here would mean two copies to keep in step.
    expect(buildCards(hass()).map((c) => c.type)).toEqual([
      "custom:solar-sanity-battery-card",
      "custom:solar-sanity-forecast-card",
    ]);
    expect(buildCards(undefined)).toHaveLength(2);
  });

  it("always offers the forecast card exactly once", () => {
    for (const entities of [{}, outlookOf("a"), { ...outlookOf("a"), ...outlookOf("b") }]) {
      const forecast = buildCards(hass(entities)).filter(
        (c) => c.type === "custom:solar-sanity-forecast-card",
      );

      expect(forecast).toHaveLength(1);
    }
  });

  it("never emits the retired status card", () => {
    const types = buildCards(hass(outlookOf("dev-a"))).map((c) => c.type);

    expect(types).not.toContain("custom:solar-sanity-card");
  });
});

describe("the strategies themselves", () => {
  it("are registered under the names Home Assistant looks for", () => {
    // `strategy: {type: "custom:solar-sanity"}` resolves to this element name.
    expect(customElements.get("ll-strategy-view-solar-sanity")).toBe(SolarSanityViewStrategy);
    expect(customElements.get("ll-strategy-dashboard-solar-sanity")).toBe(
      SolarSanityDashboardStrategy,
    );
  });

  it("the view fills itself with cards", async () => {
    const view = await SolarSanityViewStrategy.generate({}, hass(outlookOf("dev-a")));

    expect((view.cards as unknown[]).length).toBe(3);
  });

  it("the dashboard delegates to the view rather than repeating it", async () => {
    const dashboard = await SolarSanityDashboardStrategy.generate({}, hass());
    const views = dashboard.views as Array<{ strategy?: { type: string } }>;

    expect(views).toHaveLength(1);
    expect(views[0]!.strategy?.type).toBe("custom:solar-sanity");
  });

  it("the dashboard offers itself by name", () => {
    const entry = window.customStrategies?.find((s) => s.type === "solar-sanity");

    expect(entry?.strategyType).toBe("dashboard");
    expect(entry?.name).toBe("Solar Sanity");
  });

  it("registers itself once, however many times the module is imported", async () => {
    await import("./strategy");
    const matching = window.customStrategies?.filter((s) => s.type === "solar-sanity");

    expect(matching).toHaveLength(1);
  });
});
