import { describe, expect, it } from "vitest";

import {
  buildCards,
  installations,
  SolarSanityDashboardStrategy,
  SolarSanityViewStrategy,
} from "./strategy";
import type { HassEntity, HomeAssistant } from "./types/hass";

const OPTIONS = ["ok", "insufficient_data", "not_checkable", "investigating", "fault_found"];

function entity(id: string, attributes: Record<string, unknown> = {}): HassEntity {
  return { entity_id: id, state: "ok", attributes, last_updated: "" };
}

const ours = (id: string) => entity(id, { options: OPTIONS });

const hass = (states: HassEntity[]) =>
  ({ states: Object.fromEntries(states.map((s) => [s.entity_id, s])) }) as unknown as HomeAssistant;

describe("installations", () => {
  it("finds ours among a houseful of other status sensors", () => {
    const found = installations(
      hass([
        entity("sensor.frigate_status"),
        ours("sensor.solar_sanity_status"),
        entity("sensor.alarmo_status"),
      ]),
    );

    expect(found).toEqual(["sensor.solar_sanity_status"]);
  });

  it("is stable, so two houses do not swap between reloads", () => {
    const states = [ours("sensor.b_status"), ours("sensor.a_status")];

    expect(installations(hass(states))).toEqual(["sensor.a_status", "sensor.b_status"]);
  });

  it("survives being asked before Home Assistant arrives", () => {
    expect(installations(undefined)).toEqual([]);
    expect(installations({} as HomeAssistant)).toEqual([]);
  });
});

describe("buildCards", () => {
  it("puts the verdict first", () => {
    // The forecast is what you look at when the verdict is boring. Putting it
    // first would bury the answer.
    const cards = buildCards(hass([ours("sensor.solar_sanity_status")]));

    expect(cards[0]!.type).toBe("custom:solar-sanity-card");
    expect(cards[1]!.type).toBe("custom:solar-sanity-forecast-card");
  });

  it("leaves a single card to find its own entity", () => {
    const cards = buildCards(hass([ours("sensor.solar_sanity_status")]));

    expect(cards[0]!.entity).toBeUndefined();
  });

  it("names the entity when there is more than one house", () => {
    // A card that picks silently between two shows a verdict about a house the
    // reader may not be looking at.
    const cards = buildCards(hass([ours("sensor.a_status"), ours("sensor.b_status")]));

    expect(cards.map((c) => c.entity)).toEqual(["sensor.a_status", "sensor.b_status", undefined]);
  });

  it("still lays out a view when nothing is installed", () => {
    // The status card already renders "not set up yet" with a button that goes
    // and sets it up. Writing that sentence a second time here would mean two
    // copies to keep in step.
    const cards = buildCards(hass([]));

    expect(cards.map((c) => c.type)).toEqual([
      "custom:solar-sanity-card",
      "custom:solar-sanity-forecast-card",
    ]);
  });

  it("always offers the forecast card exactly once", () => {
    for (const states of [[], [ours("sensor.a_status")], [ours("sensor.a_status"), ours("sensor.b_status")]]) {
      const forecast = buildCards(hass(states)).filter(
        (c) => c.type === "custom:solar-sanity-forecast-card",
      );

      expect(forecast).toHaveLength(1);
    }
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
    const view = await SolarSanityViewStrategy.generate(
      {},
      hass([ours("sensor.solar_sanity_status")]),
    );

    expect((view.cards as unknown[]).length).toBe(2);
  });

  it("the dashboard delegates to the view rather than repeating it", async () => {
    const dashboard = await SolarSanityDashboardStrategy.generate({}, hass([]));
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
