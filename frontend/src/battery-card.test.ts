import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "./battery-card";
import type { SolarSanityBatteryCard } from "./battery-card";
import type { HassEntity, HomeAssistant } from "./types/hass";

const ZONE = "Asia/Jerusalem";
const at = (local: string) => new Date(`2026-11-07T${local}:00+02:00`).toISOString();
const next = (local: string) => new Date(`2026-11-08T${local}:00+02:00`).toISOString();

function entity(entity_id: string, state: string, attributes: Record<string, unknown> = {}): HassEntity {
  return { entity_id, state, attributes, last_updated: "" };
}

const IDS = {
  battery_full_at: "sensor.full_at",
  battery_reserve_at: "sensor.reserve_at",
  battery_lowest_before_sunrise: "sensor.lowest",
  safe_average_load: "sensor.safe_load",
  spare_solar: "sensor.spare",
} as const;

function registry(device: string, prefix = "") {
  return Object.fromEntries(
    Object.entries(IDS).map(([key, id]) => [
      `${id}${prefix}`,
      { entity_id: `${id}${prefix}`, platform: "solar_sanity", translation_key: key, device_id: device },
    ]),
  );
}

function states(prefix = "", lowestState = "35.1", extra: Record<string, unknown> = {}) {
  return {
    [`${IDS.battery_full_at}${prefix}`]: entity(`${IDS.battery_full_at}${prefix}`, "unknown"),
    [`${IDS.battery_reserve_at}${prefix}`]: entity(`${IDS.battery_reserve_at}${prefix}`, "unknown"),
    [`${IDS.battery_lowest_before_sunrise}${prefix}`]: entity(
      `${IDS.battery_lowest_before_sunrise}${prefix}`,
      lowestState,
      {
        at: next("08:00"),
        night_from: at("17:00"),
        takeover_at: next("08:00"),
        soc_pct: 41,
        reserve_pct: 35,
        target_pct: 100,
        production_from: "forecast: Home",
        trajectory: [
          { at: at("12:19"), soc: 41 },
          { at: at("17:00"), soc: 74 },
          { at: next("08:00"), soc: 35.1 },
          { at: next("12:00"), soc: 60 },
        ],
        ...extra,
      },
    ),
    [`${IDS.safe_average_load}${prefix}`]: entity(`${IDS.safe_average_load}${prefix}`, "0.75", {
      until: next("08:00"),
    }),
    [`${IDS.spare_solar}${prefix}`]: entity(`${IDS.spare_solar}${prefix}`, "0.0", {
      day_from: at("12:19"),
    }),
  };
}

function hass(overrides: Partial<HomeAssistant> = {}): HomeAssistant {
  return {
    states: states(),
    entities: registry("dev-a"),
    config: { state: "RUNNING", time_zone: ZONE },
    language: "en",
    ...overrides,
  } as unknown as HomeAssistant;
}

describe("the battery card", () => {
  let card: SolarSanityBatteryCard;

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(at("22:00")));
    card = document.createElement("solar-sanity-battery-card");
    card.setConfig({ type: "custom:solar-sanity-battery-card" });
    document.body.append(card);
  });

  afterEach(() => {
    vi.useRealTimers();
    card.remove();
  });

  const text = async () => {
    await card.updateComplete;
    return card.shadowRoot?.textContent?.replace(/\s+/g, " ").trim() ?? "";
  };

  it("waits rather than claiming anything before Home Assistant arrives", async () => {
    expect(await text()).toContain("Waiting for Home Assistant");
  });

  it("leads with the night once it has begun", async () => {
    card.hass = hass();
    const shown = await text();

    expect(shown).toContain("41%");
    expect(shown).toContain("Lasts until the sun, at 08:00");
    expect(shown).toContain("0.75 kW");
    expect(shown).toContain("until 08:00");
  });

  it("draws its shapes as SVG, so a browser paints them", async () => {
    card.hass = hass();
    await text();
    const shapes = card.shadowRoot?.querySelectorAll(".chart svg path, .chart svg line, .chart svg rect") ?? [];

    expect(shapes.length).toBeGreaterThanOrEqual(4);
    for (const shape of shapes) expect(shape.namespaceURI).toBe("http://www.w3.org/2000/svg");
  });

  it("gives the line in words to a screen reader", async () => {
    card.hass = hass();
    await text();
    const rows = card.shadowRoot?.querySelectorAll(".sr-only table tbody tr") ?? [];

    expect(rows).toHaveLength(4);
    expect(card.shadowRoot?.querySelector(".chart svg")?.getAttribute("aria-label")).toContain(
      "from 41% now",
    );
  });

  it("says plainly when the charge level is not reporting", async () => {
    const gone = Object.fromEntries(Object.values(IDS).map((id) => [id, entity(id, "unavailable")]));
    card.hass = hass({ states: gone });

    expect(await text()).toContain("charge level is not reporting");
  });

  it("shows the battery and the reason while the outlook is still learning", async () => {
    card.hass = hass({
      states: states("", "unknown", {
        trajectory: [],
        soc_pct: 64,
        reason: "Learning how much the house uses at each hour; this needs three days of consumption history.",
      }),
    });
    const shown = await text();

    expect(shown).toContain("64%");
    expect(shown).toContain("three days");
    expect(card.shadowRoot?.querySelector(".chart")).toBeNull();
  });

  it("says how to turn the outlook on when the integration has none", async () => {
    card.hass = hass({
      states: {},
      entities: {
        "sensor.forecast_tomorrow": {
          entity_id: "sensor.forecast_tomorrow",
          platform: "solar_sanity",
          translation_key: "expected_tomorrow",
          device_id: "dev-a",
        },
      },
    });

    expect(await text()).toContain("battery outlook is off");
  });

  it("says so when Solar Sanity is not set up at all", async () => {
    card.hass = hass({ states: {}, entities: {} });

    expect(await text()).toContain("not set up yet");
  });

  it("will not pick between two houses", async () => {
    card.hass = hass({
      states: { ...states(), ...states("_2") },
      entities: { ...registry("dev-a"), ...registry("dev-b", "_2") },
    });

    expect(await text()).toContain("More than one installation");
  });

  it("shows the house it was pinned to", async () => {
    card.setConfig({ type: "custom:solar-sanity-battery-card", device_id: "dev-b" });
    card.hass = hass({
      states: { ...states(), ...states("_2", "50", { soc_pct: 77 }) },
      entities: { ...registry("dev-a"), ...registry("dev-b", "_2") },
    });

    expect(await text()).toContain("77%");
  });

  it("says so when the pinned house is gone", async () => {
    card.setConfig({ type: "custom:solar-sanity-battery-card", device_id: "dev-gone" });
    card.hass = hass();

    expect(await text()).toContain("Installation not found");
  });

  it("ignores settings it does not know, and refuses only a malformed device", () => {
    expect(() =>
      card.setConfig({ type: "custom:solar-sanity-battery-card", entity: "sensor.old" }),
    ).not.toThrow();
    expect(() =>
      card.setConfig({ type: "custom:solar-sanity-battery-card", device_id: 7 as unknown as string }),
    ).toThrow();
  });

  it("offers a stub with nothing to fill in", () => {
    const ctor = customElements.get("solar-sanity-battery-card") as unknown as {
      getStubConfig(): unknown;
    };

    expect(ctor.getStubConfig()).toEqual({ type: "custom:solar-sanity-battery-card" });
  });
});
