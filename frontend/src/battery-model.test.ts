import { describe, expect, it } from "vitest";

import {
  type Outlook,
  chartShape,
  clockFor,
  duration,
  figures,
  findInstallations,
  hasOutlook,
  headline,
  power,
  powerFlow,
  readOutlook,
  socAt,
} from "./battery-model";
import type { HassEntity, HomeAssistant } from "./types/hass";

const ZONE = "Asia/Jerusalem";
const clock = clockFor("en", ZONE);

/** 2026-11-07 is UTC+2 in Jerusalem, so local = UTC + 2 h. */
const at = (local: string) => new Date(`2026-11-07T${local}:00+02:00`);
const next = (local: string) => new Date(`2026-11-08T${local}:00+02:00`);

function entity(entity_id: string, state: string, attributes: Record<string, unknown> = {}): HassEntity {
  return { entity_id, state, attributes, last_updated: "" };
}

const KEYS = {
  battery_full_at: "sensor.battery_full_at",
  battery_reserve_at: "sensor.battery_reaches_reserve_at",
  battery_lowest_before_sunrise: "sensor.lowest_battery_before_sunrise",
  safe_average_load: "sensor.safe_average_load_tonight",
  spare_solar: "sensor.spare_solar",
  battery_usable_energy: "sensor.usable_battery_energy",
  expected_use_tonight: "sensor.expected_use_tonight",
  spare_solar_power: "sensor.spare_solar_power_now",
} as const;

function registry(device = "dev-a", keys: Record<string, string> = KEYS) {
  return Object.fromEntries(
    Object.entries(keys).map(([key, id]) => [
      id,
      { entity_id: id, platform: "solar_sanity", translation_key: key, device_id: device },
    ]),
  );
}

const TRAJECTORY = [
  { at: at("12:19").toISOString(), soc: 41 },
  { at: at("13:00").toISOString(), soc: 48 },
  { at: at("17:00").toISOString(), soc: 74 },
  { at: next("02:00").toISOString(), soc: 45 },
  { at: next("08:00").toISOString(), soc: 35.1 },
  { at: next("12:00").toISOString(), soc: 60 },
];

function evening(overrides: Partial<Record<keyof typeof KEYS, HassEntity>> = {}): HomeAssistant {
  const states: Record<string, HassEntity> = {
    [KEYS.battery_full_at]: entity(KEYS.battery_full_at, "unknown", { target_pct: 100 }),
    [KEYS.battery_reserve_at]: entity(KEYS.battery_reserve_at, "unknown", { reserve_pct: 35 }),
    [KEYS.battery_lowest_before_sunrise]: entity(KEYS.battery_lowest_before_sunrise, "35.1", {
      at: next("08:00").toISOString(),
      night_from: at("17:00").toISOString(),
      takeover_at: next("08:00").toISOString(),
      soc_pct: 41,
      reserve_pct: 35,
      target_pct: 100,
      production_from: "forecast: Home",
      trajectory: TRAJECTORY,
    }),
    [KEYS.safe_average_load]: entity(KEYS.safe_average_load, "0.75", {
      until: next("08:00").toISOString(),
    }),
    [KEYS.spare_solar]: entity(KEYS.spare_solar, "0.0", { day_from: at("12:19").toISOString() }),
    [KEYS.battery_usable_energy]: entity(KEYS.battery_usable_energy, "1.66", { reserve_pct: 35 }),
    [KEYS.expected_use_tonight]: entity(KEYS.expected_use_tonight, "9.42", {
      from: at("17:00").toISOString(),
      until: next("08:00").toISOString(),
    }),
    [KEYS.spare_solar_power]: entity(KEYS.spare_solar_power, "1240", { source: "forecast" }),
  };
  for (const [key, value] of Object.entries(overrides)) states[KEYS[key as keyof typeof KEYS]] = value!;
  return {
    states,
    entities: registry(),
    config: { state: "RUNNING", time_zone: ZONE },
    language: "en",
  } as unknown as HomeAssistant;
}

function outlook(overrides: Partial<Outlook> = {}): Outlook {
  const hass = evening();
  return { ...readOutlook(hass, findInstallations(hass)[0]!), ...overrides };
}

describe("finding the sensors", () => {
  it("groups this integration's entities by device, whatever they are called", () => {
    const renamed = registry("dev-a", {
      battery_full_at: "sensor.when_is_it_full",
      battery_lowest_before_sunrise: "sensor.my_night",
    });
    const hass = {
      entities: {
        ...renamed,
        "sensor.other": { entity_id: "sensor.other", platform: "other", translation_key: "battery_full_at", device_id: "x" },
      },
    } as unknown as HomeAssistant;

    const found = findInstallations(hass);

    expect(found).toEqual([
      {
        deviceId: "dev-a",
        outlook: {
          battery_full_at: "sensor.when_is_it_full",
          battery_lowest_before_sunrise: "sensor.my_night",
        },
      },
    ]);
    expect(hasOutlook(found[0]!)).toBe(true);
  });

  it("keeps an installation without an outlook, so the card can say how to turn it on", () => {
    const hass = {
      entities: {
        "sensor.forecast_tomorrow": {
          entity_id: "sensor.forecast_tomorrow",
          platform: "solar_sanity",
          translation_key: "expected_tomorrow",
          device_id: "dev-b",
        },
      },
    } as unknown as HomeAssistant;

    const found = findInstallations(hass);

    expect(found).toHaveLength(1);
    expect(hasOutlook(found[0]!)).toBe(false);
  });

  it("orders two installations the same way every time", () => {
    const hass = {
      entities: { ...registry("dev-b", { battery_full_at: "sensor.b" }), ...registry("dev-a", { battery_full_at: "sensor.a" }) },
    } as unknown as HomeAssistant;

    expect(findInstallations(hass).map((i) => i.deviceId)).toEqual(["dev-a", "dev-b"]);
  });

  it("finds nothing in a hass without a registry", () => {
    expect(findInstallations({ states: {} } as unknown as HomeAssistant)).toEqual([]);
    expect(findInstallations(undefined)).toEqual([]);
  });
});

describe("reading the outlook", () => {
  it("reads every figure from the sensors that publish it", () => {
    const read = outlook();

    expect(read.socPct).toBe(41);
    expect(read.lowestPct).toBe(35.1);
    expect(read.safeLoadKw).toBe(0.75);
    expect(read.spareKwh).toBe(0);
    expect(read.reservePct).toBe(35);
    expect(read.targetPct).toBe(100);
    expect(read.forecastUsed).toBe(true);
    expect(read.fullAt).toBeNull();
    expect(read.trajectory).toHaveLength(6);
    expect(read.reason).toBeNull();
  });

  it("calls the charge level gone only when every sensor is unavailable", () => {
    const gone = Object.fromEntries(
      Object.keys(KEYS).map((key) => [key, entity(KEYS[key as keyof typeof KEYS], "unavailable")]),
    );
    const hass = evening(gone);

    expect(readOutlook(hass, findInstallations(hass)[0]!).unavailable).toBe(true);
    expect(outlook().unavailable).toBe(false);
  });

  it("carries the reason when nothing could be projected", () => {
    const hass = evening({
      battery_lowest_before_sunrise: entity(KEYS.battery_lowest_before_sunrise, "unknown", {
        soc_pct: 64,
        reason: "Learning how much the house uses at each hour; this needs three days of consumption history.",
        trajectory: [],
      }),
    });
    const read = readOutlook(hass, findInstallations(hass)[0]!);

    expect(read.reason).toContain("three days");
    expect(read.socPct).toBe(64);
  });

  it("does not mistake a reason about one figure for no projection", () => {
    const hass = evening({
      battery_lowest_before_sunrise: entity(KEYS.battery_lowest_before_sunrise, "unknown", {
        reason: "No night falls within the forecast.",
        trajectory: TRAJECTORY,
      }),
    });

    expect(readOutlook(hass, findInstallations(hass)[0]!).reason).toBeNull();
  });

  it("falls back to the trajectory for the charge on an older integration", () => {
    const hass = evening({
      battery_lowest_before_sunrise: entity(KEYS.battery_lowest_before_sunrise, "35.1", {
        trajectory: TRAJECTORY,
      }),
    });

    expect(readOutlook(hass, findInstallations(hass)[0]!).socPct).toBe(41);
  });
});

describe("the clock", () => {
  it("tells the time in the house's zone, not the browser's", () => {
    expect(clock.time(new Date("2026-11-07T12:35:00Z"))).toBe("14:35");
    expect(clock.hour(new Date("2026-11-07T22:00:00Z"))).toBe(0);
  });

  it("names the day relative to now", () => {
    const now = at("23:30");
    expect(clock.day(at("12:00"), now)).toBe("today");
    expect(clock.day(next("01:00"), now)).toBe("tomorrow");
  });

  it("survives a zone name the browser does not know", () => {
    expect(() => clockFor("en", "Not/AZone").time(new Date())).not.toThrow();
  });

  it("spells durations the way people say them", () => {
    expect(duration(130 * 60_000)).toBe("2 h 10 min");
    expect(duration(45 * 60_000)).toBe("45 min");
    expect(duration(3 * 3_600_000)).toBe("3 h");
    expect(duration(-5)).toBe("0 min");
  });
});

describe("the headline", () => {
  it("warns first when the battery will reach its reserve before the sun", () => {
    const lead = headline(
      outlook({ reserveAt: next("04:10"), takeoverAt: next("08:00") }),
      at("22:00"),
      clock,
    );

    expect(lead).toEqual({
      text: "Reaches the reserve at 04:10",
      detail: "In 6 h 10 min, the sun takes over at 08:00",
      tone: "warn",
    });
  });

  it("at night, says how long the battery lasts", () => {
    const lead = headline(outlook(), at("22:00"), clock);

    expect(lead).toEqual({
      text: "Lasts until the sun, at 08:00",
      detail: "Lowest 35% at 08:00",
      tone: "good",
    });
  });

  it("by day, says when the battery fills", () => {
    const lead = headline(outlook({ fullAt: at("14:35") }), at("12:25"), clock);

    expect(lead).toEqual({ text: "Full at 14:35", detail: "In 2 h 10 min", tone: "good" });
  });

  it("does not say 'in' about a moment that has passed", () => {
    const lead = headline(outlook({ fullAt: at("11:40") }), at("12:25"), clock);

    expect(lead).toEqual({ text: "Full", detail: "Since 11:40", tone: "good" });
  });

  it("with a forecast that does not fill it, says how high it gets", () => {
    const lead = headline(outlook(), at("12:25"), clock);

    expect(lead).toEqual({ text: "Will not fill today", detail: "Peaks at 74% at 17:00", tone: "neutral" });
  });

  it("without a forecast, claims nothing about the day", () => {
    const lead = headline(outlook({ forecastUsed: false }), at("12:25"), clock);

    expect(lead.text).toBe("Lowest tonight 35%");
  });
});

describe("the figures", () => {
  it("shows a dash, never a zero, where there is no answer", () => {
    const shown = figures(outlook({ safeLoadKw: null, safeUntil: null }), at("12:25"), clock);

    expect(shown[0]).toEqual({ label: "Safe load tonight", value: "—", sub: null });
    expect(shown[1]).toEqual({ label: "Lowest before sunrise", value: "35%", sub: "at 08:00" });
    expect(shown[2]).toEqual({ label: "Spare sun", value: "0.0 kWh", sub: "today" });
  });

  it("says what the battery holds, what tonight takes and the sun going spare now", () => {
    // 0.34.0: the owner asked for figures to act on, beside the three above.
    const shown = figures(outlook(), at("12:25"), clock);

    expect(shown).toHaveLength(6);
    expect(shown[3]).toEqual({ label: "Usable now", value: "1.7 kWh", sub: "above 35%" });
    expect(shown[4]).toEqual({ label: "Tonight's use", value: "9.4 kWh", sub: "until 08:00" });
    expect(shown[5]).toEqual({ label: "Spare sun now", value: "1.2 kW", sub: null });
  });

  it("shows a dash for the new ones too while they have no answer", () => {
    const shown = figures(
      outlook({ usableKwh: null, tonightKwh: null, tonightUntil: null, sparePowerW: null }),
      at("12:25"),
      clock,
    );

    expect(shown.slice(3).map((figure) => figure.value)).toEqual(["—", "—", "—"]);
  });
});

describe("the power flow", () => {
  const live = (states: Record<string, HassEntity>) =>
    ({ states, entities: {}, config: { state: "RUNNING" } }) as unknown as HomeAssistant;

  it("reads each named sensor in its own unit", () => {
    const hass = live({
      "sensor.pv": entity("sensor.pv", "2438", { unit_of_measurement: "W" }),
      "sensor.house": entity("sensor.house", "0.436", { unit_of_measurement: "kW" }),
      "sensor.grid": entity("sensor.grid", "0", { unit_of_measurement: "W" }),
    });

    expect(
      powerFlow(hass, {
        solar: "sensor.pv",
        house: "sensor.house",
        grid: "sensor.grid",
        batteryChargingPositive: true,
      }),
    ).toEqual([
      { label: "Solar", value: "2.4 kW" },
      { label: "House", value: "436 W" },
      { label: "Grid", value: "0 W" },
    ]);
  });

  it("says which way the battery is going, whichever sign its sensor uses", () => {
    const hass = live({
      "sensor.battery": entity("sensor.battery", "-1900", { unit_of_measurement: "W" }),
    });

    expect(
      powerFlow(hass, { battery: "sensor.battery", batteryChargingPositive: true }),
    ).toEqual([{ label: "Battery", value: "discharging 1.9 kW" }]);
    expect(
      powerFlow(hass, { battery: "sensor.battery", batteryChargingPositive: false }),
    ).toEqual([{ label: "Battery", value: "charging 1.9 kW" }]);
  });

  it("calls a few watts idle rather than a direction", () => {
    const hass = live({
      "sensor.battery": entity("sensor.battery", "8", { unit_of_measurement: "W" }),
    });

    expect(powerFlow(hass, { battery: "sensor.battery", batteryChargingPositive: true })).toEqual([
      { label: "Battery", value: "idle" },
    ]);
  });

  it("leaves out what is not reporting, never showing it as zero", () => {
    const hass = live({
      "sensor.pv": entity("sensor.pv", "unavailable", { unit_of_measurement: "W" }),
      "sensor.total": entity("sensor.total", "1509.8", { unit_of_measurement: "kWh" }),
    });

    expect(
      powerFlow(hass, { solar: "sensor.pv", house: "sensor.total", batteryChargingPositive: true }),
    ).toEqual([]);
    expect(powerFlow(hass, null)).toEqual([]);
  });

  it("is read from the lowest-battery sensor's power_sources", () => {
    const hass = evening({
      battery_lowest_before_sunrise: entity(KEYS.battery_lowest_before_sunrise, "35.1", {
        trajectory: TRAJECTORY,
        power_sources: { solar: "sensor.pv", battery_charging_positive: false },
      }),
    });
    const read = readOutlook(hass, findInstallations(hass)[0]!);

    expect(read.powerSources).toEqual({
      solar: "sensor.pv",
      house: undefined,
      grid: undefined,
      battery: undefined,
      batteryChargingPositive: false,
    });
  });

  it("writes watts as a person reads them", () => {
    expect([power(436), power(999.6), power(1240), power(12_500)]).toEqual([
      "436 W",
      "1.0 kW",
      "1.2 kW",
      "12.5 kW",
    ]);
  });
});

describe("the chart", () => {
  it("draws the projection on a fixed 0 to 100 scale", () => {
    const shape = chartShape(outlook(), clock)!;

    expect(shape.line.startsWith("M0,70.8 L")).toBe(true);
    expect(shape.reserve).toBeCloseTo(0.65);
    expect(shape.target).toBe(0);
    expect(shape.start.x).toBe(0);
    expect(shape.start.y).toBeCloseTo(0.59);
  });

  it("shades the night from its start to the takeover", () => {
    const shape = chartShape(outlook(), clock)!;
    const span = next("12:00").getTime() - at("12:19").getTime();

    expect(shape.night!.from).toBeCloseTo((at("17:00").getTime() - at("12:19").getTime()) / span);
    expect(shape.night!.to).toBeCloseTo((next("08:00").getTime() - at("12:19").getTime()) / span);
  });

  it("labels the hours every six on the house's clock", () => {
    const labels = chartShape(outlook(), clock)!.hours.map((hour) => hour.label);

    expect(labels).toEqual(["18", "00", "06", "12"]);
  });

  it("marks the moment the battery fills, on the line", () => {
    const shape = chartShape(outlook({ fullAt: at("17:00") }), clock)!;

    expect(shape.marks).toEqual([{ kind: "full", x: expect.closeTo(0.198, 2), y: expect.closeTo(0.26, 2) }]);
  });

  it("needs two points to draw anything", () => {
    expect(chartShape(outlook({ trajectory: TRAJECTORY.slice(0, 1).map((p) => ({ at: new Date(p.at), soc: p.soc })) }), clock)).toBeNull();
  });

  it("reads the charge between two points", () => {
    const points = outlook().trajectory;

    expect(socAt(points, new Date(at("12:19").getTime() + (at("13:00").getTime() - at("12:19").getTime()) / 2))).toBeCloseTo(44.5);
    expect(socAt(points, at("09:00"))).toBeNull();
  });
});
