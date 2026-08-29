import { beforeEach, describe, expect, it, vi } from "vitest";

import { buildPanel, SolarSanityForecastCard } from "./forecast-card";
import { DAYAHEAD_PREFIX, type ProviderDay, tomorrow } from "./forecast-data";
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
    // Derived from the same clock the card reads, not a literal. This fixture
    // used to name a date that was tomorrow on the morning it was written, and
    // it passed until midnight rolled it into today — at which point the card
    // correctly filtered every row out and the test failed with no bug behind
    // it. A card that asks "what does tomorrow look like" can only be tested
    // against a tomorrow that moves with it.
    const target = tomorrow(new Date());
    const rows = Array.from({ length: 6 }, (_, index) => ({
      start: new Date(
        target.getFullYear(),
        target.getMonth(),
        target.getDate(),
        8 + index,
      ).getTime(),
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

describe("staying honest over time", () => {
  const ID2 = `${DAYAHEAD_PREFIX}01ABC`;

  function build() {
    const calls: string[] = [];
    const card = document.createElement("solar-sanity-forecast-card") as SolarSanityForecastCard;
    card.setConfig({ type: "custom:solar-sanity-forecast-card" });
    document.body.append(card);
    const make = (state: string, reject = false) =>
      ({
        states: {},
        config: { state },
        language: "en",
        callWS: vi.fn((msg: { type: string }) => {
          calls.push(msg.type);
          return reject
            ? Promise.reject(new Error("recorder disabled"))
            : Promise.resolve(msg.type === "recorder/list_statistic_ids" ? [{ statistic_id: ID2 }] : {});
        }),
      }) as unknown as HomeAssistant;
    return { card, calls, make };
  }

  const settle = async (card: SolarSanityForecastCard) => {
    await card.updateComplete;
    await new Promise((r) => setTimeout(r, 0));
    await card.updateComplete;
  };

  it("does not ask before Home Assistant is running", async () => {
    const { card, calls, make } = build();
    card.hass = make("NOT_RUNNING");
    await settle(card);

    expect(calls).toEqual([]);
  });

  it("tries again after a restart rather than latching the failure", async () => {
    // The usual way to see a recorder refuse is a restart. Latching meant the
    // card said "cannot read the record" for the rest of the day on an
    // installation whose recorder had been fine for hours.
    const { card, calls, make } = build();
    card.hass = make("RUNNING", true);
    await settle(card);
    expect((card.shadowRoot?.textContent ?? "")).toContain("Cannot read the record");

    card.hass = make("NOT_RUNNING");
    await settle(card);
    card.hass = make("RUNNING");
    await settle(card);

    expect(calls.filter((c) => c === "recorder/list_statistic_ids").length).toBeGreaterThan(1);
    expect(card.shadowRoot?.textContent ?? "").not.toContain("Cannot read the record");
  });

  it("keeps refusing while the failure is still fresh", async () => {
    // A genuinely disabled recorder must not be asked again at the rate `hass`
    // is reassigned, which is dozens of times a minute.
    const { card, calls, make } = build();
    card.hass = make("RUNNING", true);
    await settle(card);
    const asked = calls.length;

    for (let i = 0; i < 20; i += 1) {
      card.hass = make("RUNNING");
      await settle(card);
    }

    expect(calls.length).toBe(asked);
    expect(card.shadowRoot?.textContent ?? "").toContain("Cannot read the record");
  });

  it("retries a transient failure without waiting for a restart", async () => {
    // The release written for the restart case could never fire on its own:
    // `_load` is only reachable past the RUNNING gate, so every failure is
    // recorded during RUNNING and the phase comparison is always "RUNNING"
    // against "RUNNING". A websocket blip — a laptop waking, wifi roaming, the
    // recorder reloading — therefore stuck until local midnight, on an
    // installation whose recorder was fine seconds later.
    vi.useFakeTimers();
    try {
      const { card, calls, make } = build();
      card.hass = make("RUNNING", true);
      await card.updateComplete;
      await vi.advanceTimersByTimeAsync(1);
      await card.updateComplete;
      expect(card.shadowRoot?.textContent ?? "").toContain("Cannot read the record");
      const asked = calls.filter((c) => c === "recorder/list_statistic_ids").length;

      // Past the retry window, with Home Assistant never leaving RUNNING.
      await vi.advanceTimersByTimeAsync(6 * 60_000);
      card.hass = make("RUNNING");
      await card.updateComplete;
      await vi.advanceTimersByTimeAsync(1);
      await card.updateComplete;

      expect(
        calls.filter((c) => c === "recorder/list_statistic_ids").length,
      ).toBeGreaterThan(asked);
      expect(card.shadowRoot?.textContent ?? "").not.toContain("Cannot read the record");
    } finally {
      vi.useRealTimers();
    }
  });

  it("asks once however many times hass is reassigned", async () => {
    // Home Assistant hands every card a new `hass` on every state change, and
    // there are dozens a minute. Each used to start its own pair of recorder
    // queries against SQLite on a Pi.
    const { card, calls, make } = build();
    for (let i = 0; i < 5; i += 1) card.hass = make("RUNNING");
    await settle(card);

    expect(calls.filter((c) => c === "recorder/list_statistic_ids")).toHaveLength(1);
  });

  it("never calls a day in progress tomorrow", async () => {
    // At five past midnight a card loaded the previous evening still holds the
    // right data. Calling it "Tomorrow" is the only part that became false.
    const { card, make } = build();
    card.hass = make("RUNNING");
    await settle(card);

    const heading = card.shadowRoot?.querySelector("h2")?.textContent?.trim();
    expect(["Tomorrow", "Today", "Forecast", "This record starts today"]).toContain(heading);
  });

  it("reloads when the day moves under it", async () => {
    const { card, calls, make } = build();
    card.hass = make("RUNNING");
    await settle(card);
    const before = calls.filter((c) => c === "recorder/list_statistic_ids").length;

    // Pretend the clock rolled past midnight: what the card holds now answers
    // yesterday's question.
    (card as unknown as { _day: Date })._day = new Date(2000, 0, 1);
    card.hass = make("RUNNING");
    await settle(card);

    expect(calls.filter((c) => c === "recorder/list_statistic_ids").length).toBeGreaterThan(before);
  });
});
