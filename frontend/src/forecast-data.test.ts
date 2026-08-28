import { describe, expect, it, vi } from "vitest";

import type { HomeAssistant } from "./types/hass";
import {
  DAYAHEAD_PREFIX,
  dayAheadArchives,
  hoursOn,
  loadDayAhead,
  localDayBounds,
  peak,
  providerLabel,
  toHours,
  tomorrow,
  total,
} from "./forecast-data";

const ID = `${DAYAHEAD_PREFIX}01ABC`;

describe("dayAheadArchives", () => {
  it("keeps only the day-ahead archives", () => {
    // The rolling archive holds the latest revision of every hour, so for any
    // hour already past it holds a figure issued after that hour ended. It is
    // the right thing to display and the wrong thing to call a forecast.
    const found = dayAheadArchives([
      { statistic_id: ID },
      { statistic_id: "solar_sanity:forecast_01ABC" },
      { statistic_id: "sensor.some_meter" },
    ]);

    expect(found.map((m) => m.statistic_id)).toEqual([ID]);
  });

  it("orders them so two providers do not swap on a reload", () => {
    const found = dayAheadArchives([
      { statistic_id: `${DAYAHEAD_PREFIX}zzz` },
      { statistic_id: `${DAYAHEAD_PREFIX}aaa` },
    ]);

    expect(found.map((m) => m.statistic_id)).toEqual([
      `${DAYAHEAD_PREFIX}aaa`,
      `${DAYAHEAD_PREFIX}zzz`,
    ]);
  });

  it("finds nothing in an empty recorder", () => {
    expect(dayAheadArchives([])).toEqual([]);
  });
});

describe("providerLabel", () => {
  it("strips the suffix the integration adds", () => {
    expect(providerLabel({ statistic_id: ID, name: "Forecast.Solar forecast, a day ahead" })).toBe(
      "Forecast.Solar",
    );
  });

  it("keeps a name that does not carry it", () => {
    expect(providerLabel({ statistic_id: ID, name: "Something else" })).toBe("Something else");
  });

  it("falls back to the id rather than to a blank heading", () => {
    // An unfamiliar id at least says which archive this is. An empty heading
    // says the card is broken.
    expect(providerLabel({ statistic_id: ID })).toBe("01ABC");
    expect(providerLabel({ statistic_id: ID, name: "   " })).toBe("01ABC");
  });
});

describe("toHours", () => {
  it("reads state and nothing else", () => {
    const hours = toHours([{ start: Date.UTC(2026, 7, 28, 6), state: 1.5 }]);

    expect(hours).toHaveLength(1);
    expect(hours[0]!.kwh).toBe(1.5);
  });

  it("drops an hour with no state rather than reading it as zero", () => {
    // An hour nobody forecast and an hour forecast to produce nothing are
    // different facts. On a chart the second draws a line to the floor.
    const hours = toHours([
      { start: 1, state: null },
      { start: 2 },
      { start: 3, state: 2 },
    ]);

    expect(hours.map((h) => h.kwh)).toEqual([2]);
  });

  it("drops a value that is not a finite number", () => {
    const hours = toHours([{ start: 1, state: Number.NaN }]);

    expect(hours).toEqual([]);
  });

  it("sorts by time, because a chart draws in order", () => {
    const hours = toHours([
      { start: 300, state: 3 },
      { start: 100, state: 1 },
      { start: 200, state: 2 },
    ]);

    expect(hours.map((h) => h.kwh)).toEqual([1, 2, 3]);
  });

  it("survives an absent series", () => {
    expect(toHours(undefined)).toEqual([]);
  });
});

describe("localDayBounds", () => {
  it("runs local midnight to local midnight", () => {
    const { start, end } = localDayBounds(new Date(2026, 7, 28, 13, 45));

    expect(start.getHours()).toBe(0);
    expect(start.getDate()).toBe(28);
    expect(end.getDate()).toBe(29);
  });

  it("crosses a month end", () => {
    const { end } = localDayBounds(new Date(2026, 7, 31, 9));

    expect(end.getMonth()).toBe(8);
    expect(end.getDate()).toBe(1);
  });
});

describe("tomorrow", () => {
  it("is the next local day at midnight", () => {
    const day = tomorrow(new Date(2026, 7, 28, 23, 30));

    expect(day.getDate()).toBe(29);
    expect(day.getHours()).toBe(0);
  });

  it("rolls over a year end", () => {
    const day = tomorrow(new Date(2026, 11, 31, 12));

    expect(day.getFullYear()).toBe(2027);
    expect(day.getMonth()).toBe(0);
    expect(day.getDate()).toBe(1);
  });
});

describe("hoursOn", () => {
  const hours = [
    { start: new Date(2026, 7, 28, 23), kwh: 1 },
    { start: new Date(2026, 7, 29, 0), kwh: 2 },
    { start: new Date(2026, 7, 29, 23), kwh: 3 },
    { start: new Date(2026, 7, 30, 0), kwh: 4 },
  ];

  it("takes the day it was asked for and no neighbours", () => {
    expect(hoursOn(hours, new Date(2026, 7, 29)).map((h) => h.kwh)).toEqual([2, 3]);
  });

  it("includes local midnight and excludes the next one", () => {
    const kept = hoursOn(hours, new Date(2026, 7, 29));

    expect(kept[0]!.start.getHours()).toBe(0);
    expect(kept.at(-1)!.start.getDate()).toBe(29);
  });
});

describe("total and peak", () => {
  const hours = [
    { start: new Date(), kwh: 1.5 },
    { start: new Date(), kwh: 3.25 },
  ];

  it("adds the hours up", () => {
    expect(total(hours)).toBeCloseTo(4.75);
  });

  it("is zero for nothing, which is not the same as absent", () => {
    expect(total([])).toBe(0);
    expect(peak([])).toBe(0);
  });

  it("finds the largest hour", () => {
    expect(peak(hours)).toBe(3.25);
  });
});

describe("loadDayAhead", () => {
  const hass = (responses: Record<string, unknown>) =>
    ({
      callWS: vi.fn((msg: { type: string }) => Promise.resolve(responses[msg.type])),
    }) as unknown as HomeAssistant;

  it("asks for nothing when there is no archive", async () => {
    // `statistic_ids` is required and must hold at least one id, so calling
    // with an empty list is an error rather than an empty answer.
    const ha = hass({ "recorder/list_statistic_ids": [] });

    expect(await loadDayAhead(ha, new Date(2026, 7, 29))).toEqual([]);
    expect(ha.callWS).toHaveBeenCalledTimes(1);
  });

  it("asks only for the day-ahead ids", async () => {
    const ha = hass({
      "recorder/list_statistic_ids": [
        { statistic_id: ID, name: "Forecast.Solar forecast, a day ahead" },
        { statistic_id: "solar_sanity:forecast_01ABC", name: "Forecast.Solar forecast" },
      ],
      "recorder/statistics_during_period": {},
    });

    await loadDayAhead(ha, new Date(2026, 7, 29));
    const second = (ha.callWS as unknown as { mock: { calls: [{ statistic_ids: string[] }][] } })
      .mock.calls[1]![0];

    expect(second.statistic_ids).toEqual([ID]);
  });

  it("reads state, by the hour, in kWh", async () => {
    const ha = hass({
      "recorder/list_statistic_ids": [{ statistic_id: ID }],
      "recorder/statistics_during_period": {},
    });

    await loadDayAhead(ha, new Date(2026, 7, 29));
    const second = (
      ha.callWS as unknown as {
        mock: { calls: [{ types: string[]; period: string; units: unknown }][] };
      }
    ).mock.calls[1]![0];

    expect(second.types).toEqual(["state"]);
    expect(second.period).toBe("hour");
    expect(second.units).toEqual({ energy: "kWh" });
  });

  it("returns a provider with no hours rather than dropping it", async () => {
    // "This archive starts today" and "there is no archive" are different
    // sentences, and the card says both.
    const ha = hass({
      "recorder/list_statistic_ids": [{ statistic_id: ID, name: "X forecast, a day ahead" }],
      "recorder/statistics_during_period": {},
    });

    const days = await loadDayAhead(ha, new Date(2026, 7, 29));

    expect(days).toHaveLength(1);
    expect(days[0]!.label).toBe("X");
    expect(days[0]!.hours).toEqual([]);
  });

  it("keeps only the requested day even if the recorder returns more", async () => {
    const ha = hass({
      "recorder/list_statistic_ids": [{ statistic_id: ID }],
      "recorder/statistics_during_period": {
        [ID]: [
          { start: new Date(2026, 7, 28, 12).getTime(), state: 9 },
          { start: new Date(2026, 7, 29, 12).getTime(), state: 4 },
        ],
      },
    });

    const days = await loadDayAhead(ha, new Date(2026, 7, 29));

    expect(days[0]!.hours.map((h) => h.kwh)).toEqual([4]);
  });
});
