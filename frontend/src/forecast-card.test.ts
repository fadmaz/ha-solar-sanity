import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildPanel, SolarSanityForecastCard } from "./forecast-card";
import { DAYAHEAD_PREFIX, FORECAST_PREFIX, type CorrectedForecast } from "./forecast-data";
import type { HomeAssistant } from "./types/hass";

const DEVICE = "sensor.corrected";
const PROVIDER = FORECAST_PREFIX + "first";
const NOW = new Date("2026-11-10T12:00:00Z");
const TOMORROW = new Date("2026-11-11T00:00:00Z");
const VALID_UNTIL = "2026-11-10T14:30:00Z";

function data(points: [number, number][], day = TOMORROW): CorrectedForecast {
  return {
    latestId: PROVIDER, label: "Forecast.Solar", factor: 0.4, byKindOfDay: true,
    validUntil: Date.parse(VALID_UNTIL),
    hours: points.map(([hour, kwh]) => ({start: new Date(day.getTime() + hour * 3_600_000), kwh})),
  };
}

describe("forecast geometry", () => {
  it("uses the exact corrected hours and leaves holes in proportion to elapsed time", () => {
    const panel = buildPanel(data([[8, 0.3], [9, 1.2], [12, 2.8], [13, 1.7], [15, 0]]), TOMORROW, "UTC");
    expect(panel.kwh).toBe(6);
    expect(panel.line.match(/M/g)).toHaveLength(2);
    expect(panel.area.match(/Z/g)).toHaveLength(2);
    expect(panel.isolated).toHaveLength(1);
    const [a, b, c] = panel.labels;
    expect((c!.x - b!.x) / (b!.x - a!.x)).toBeCloseTo(3, 1);
  });

  it.each([["Asia/Kolkata", "11:30"], ["Asia/Kathmandu", "11:45"]])(
    "keeps the minute of a UTC hour in %s", (zone, label) => {
      const panel = buildPanel(data([[6, 1]]), TOMORROW, zone);
      expect(panel.times).toEqual([label]);
      expect(panel.labels[0]?.text).toBe(label);
    },
  );

  it("retains two distinct positions and offsets for the repeated hour", () => {
    const day = new Date("2026-11-01T04:00:00Z");
    const panel = buildPanel(data([[0, 1], [1, 2], [2, 3], [3, 4]], day), day, "America/New_York");
    expect(panel.times.slice(1, 3)).toEqual(["01:00 GMT-4", "01:00 GMT-5"]);
    expect(panel.labels[2]!.x).toBeGreaterThan(panel.labels[1]!.x);
  });

  it("keeps elapsed time continuous when the local clock skips an hour", () => {
    const day = new Date("2026-03-08T05:00:00Z");
    const panel = buildPanel(data([[0, 1], [1, 2], [2, 3], [3, 4]], day), day, "America/New_York");
    expect(panel.times).toEqual(["00:00", "01:00", "03:00", "04:00"]);
    expect(panel.line.match(/M/g)).toHaveLength(1);
  });
});

function sensor(overrides: Record<string, unknown> = {}, state = "1.0") {
  return {state, attributes: {
    forecast_statistic_id: PROVIDER,
    forecast_for_date: "2026-11-11",
    forecast_valid_until: VALID_UNTIL,
    corrected_hours: [
      {start: "2026-11-11T06:00:00Z", kwh: 0.2},
      {start: "2026-11-11T07:00:00Z", kwh: 0.8},
    ],
    provider: "Forecast.Solar", factor: 0.4, days: 15, basis: "learned",
    ...overrides,
  }};
}

function hass(states: Record<string, ReturnType<typeof sensor>> = {}, zone = "UTC"): HomeAssistant {
  return {
    config: {state: "RUNNING", time_zone: zone},
    states,
    entities: Object.fromEntries(Object.keys(states).map(entity_id => [
      entity_id, {entity_id, platform: "solar_sanity", translation_key: "expected_tomorrow_corrected"},
    ])),
    callWS: vi.fn(),
  } as unknown as HomeAssistant;
}

describe("the forecast card", () => {
  let card: SolarSanityForecastCard;
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    card = document.createElement("solar-sanity-forecast-card") as SolarSanityForecastCard;
    card.setConfig({type: "custom:solar-sanity-forecast-card"});
    document.body.append(card);
  });

  afterEach(() => {
    card.remove();
    vi.useRealTimers();
  });

  async function rendered() {
    await card.updateComplete;
    return card.shadowRoot?.textContent ?? "";
  }

  it("renders published corrected hours with no Recorder query", async () => {
    card.hass = hass({[DEVICE]: sensor()}, "Asia/Kathmandu");
    expect(await rendered()).toContain("1.0 kWh");
    expect(await rendered()).toContain("Corrected for your roof");
    expect(await rendered()).toContain("11:45");
    expect(card.shadowRoot?.querySelector("svg")?.getAttribute("aria-label")).toContain("expected from your roof");
    expect(card.hass.callWS).not.toHaveBeenCalled();
  });

  it("hides the total while learning and takes the fresh hours when they arrive", async () => {
    card.hass = hass({[DEVICE]: sensor({reason: "Learning your roof."}, "unknown")});
    expect(await rendered()).toContain("Learning your roof.");
    expect(card.shadowRoot?.querySelector("svg")).toBeNull();
    card.hass = hass({[DEVICE]: sensor()});
    expect(await rendered()).toContain("1.0 kWh");
  });

  it("expires while the dashboard sits idle, then recovers with a new forecast", async () => {
    card.hass = hass({[DEVICE]: sensor()});
    expect(await rendered()).toContain("1.0 kWh");
    await vi.advanceTimersByTimeAsync(Date.parse(VALID_UNTIL) - NOW.getTime() + 1);
    expect(await rendered()).toContain("fresh corrected forecast");
    expect(card.shadowRoot?.querySelector("svg")).toBeNull();
    card.hass = hass({[DEVICE]: sensor({forecast_valid_until: "2026-11-10T16:00:00Z"})});
    expect(await rendered()).toContain("1.0 kWh");
  });

  it("drops yesterday's data when the house's time zone changes", async () => {
    vi.setSystemTime(new Date("2026-11-10T23:30:00Z"));
    const states = {[DEVICE]: sensor({forecast_valid_until: "2026-11-11T01:30:00Z"})};
    card.hass = hass(states);
    expect(await rendered()).toContain("1.0 kWh");
    card.hass = hass(states, "Asia/Jerusalem");
    expect(await rendered()).toContain("fresh corrected forecast");
    expect(card.shadowRoot?.querySelector("svg")).toBeNull();
  });

  it("preserves a configured provider ID and never chooses an ambiguous roof", async () => {
    card.setConfig({type: "custom:solar-sanity-forecast-card", provider: DAYAHEAD_PREFIX + "first"});
    const second = sensor({forecast_statistic_id: FORECAST_PREFIX + "second", provider: "Another provider"});
    card.hass = hass({[DEVICE]: sensor(), "sensor.other": second});
    expect(await rendered()).toContain("Forecast.Solar");
    expect(await rendered()).not.toContain("Another provider");
    card.hass = hass({[DEVICE]: sensor(), "sensor.other": sensor()});
    expect(await rendered()).toContain("Several installations share this provider");
    expect(card.shadowRoot?.querySelector("svg")).toBeNull();
  });

  it("distinguishes explicit zero from absent hours and skips other dates", async () => {
    card.hass = hass({[DEVICE]: sensor({corrected_hours: [
      {start: "2026-11-11T06:00:00Z", kwh: 0},
      {start: "2026-11-12T06:00:00Z", kwh: 5},
      {start: "invalid", kwh: 2},
    ]}, "0")});
    expect(await rendered()).toContain("0.0 kWh");
    expect(card.shadowRoot?.querySelectorAll("table tr")).toHaveLength(1);
    card.hass = hass({[DEVICE]: sensor({corrected_hours: []})});
    expect(await rendered()).toContain("No corrected hours");
    expect(card.shadowRoot?.querySelector("svg")).toBeNull();
  });

  it("shows a start-up message until Home Assistant can supply current evidence", async () => {
    const starting = hass();
    starting.config.state = "STARTING";
    card.hass = starting;
    expect(await rendered()).toContain("Home Assistant is still starting");
    expect(card.getGridOptions().rows).toBe("auto");
  });
});
