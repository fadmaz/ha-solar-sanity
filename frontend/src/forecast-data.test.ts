import { describe, expect, it } from "vitest";

import {
  DAYAHEAD_PREFIX,
  dayAheadArchives,
  localDayBounds,
  peak,
  providerLabel,
  tomorrow,
  total,
} from "./forecast-data";

const ID = `${DAYAHEAD_PREFIX}01ABC`;

describe("dayAheadArchives", () => {
  it("keeps only the day-ahead archives", () => {
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
  it("asks about the house's next day when the browser is still on yesterday", () => {
    const now = new Date("2026-11-10T23:30:00Z");
    expect(tomorrow(now, "Asia/Jerusalem").toISOString()).toBe("2026-11-11T22:00:00.000Z");
    expect(tomorrow(now, "America/Los_Angeles").toISOString()).toBe("2026-11-11T08:00:00.000Z");
  });

  it.each([
    ["2026-03-08T12:00:00Z", 23, "2026-03-08T05:00:00.000Z", "2026-03-09T04:00:00.000Z"],
    ["2026-11-01T12:00:00Z", 25, "2026-11-01T04:00:00.000Z", "2026-11-02T05:00:00.000Z"],
  ])("uses calendar midnights on the DST day %s", (instant, length, first, last) => {
    const { start, end } = localDayBounds(new Date(instant), "America/New_York");
    expect(start.toISOString()).toBe(first);
    expect(end.toISOString()).toBe(last);
    expect((end.getTime() - start.getTime()) / 3_600_000).toBe(length);
  });

  it("falls back to the browser when the configured zone is invalid", () => {
    const now = new Date("2026-11-10T23:30:00Z");
    expect(tomorrow(now, "not/a-zone")).toEqual(tomorrow(now));
  });

  it("crosses the house's year boundary without using the browser's date", () => {
    expect(tomorrow(new Date("2026-12-31T21:30:00Z"), "Asia/Jerusalem").toISOString())
      .toBe("2026-12-31T22:00:00.000Z");
  });
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
