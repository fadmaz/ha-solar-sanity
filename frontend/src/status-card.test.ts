import { beforeEach, describe, expect, it } from "vitest";

import { isStatusEntity, leadSentence, SolarSanityCard, verdictFor } from "./status-card";
import type { HassEntity, HomeAssistant } from "./types/hass";

const OPTIONS = ["ok", "insufficient_data", "not_checkable", "investigating", "fault_found"];

function entity(id: string, state: string, attributes: Record<string, unknown> = {}): HassEntity {
  return { entity_id: id, state, attributes, last_updated: "" };
}

/** Ours, however it has been renamed. */
const ours = (id = "sensor.solar_sanity_status", state = "ok") =>
  entity(id, state, { options: OPTIONS, days_of_data: 30 });

describe("isStatusEntity", () => {
  it("recognises our entity by what it publishes", () => {
    expect(isStatusEntity(ours())).toBe(true);
  });

  it("still recognises it after a rename", () => {
    // Renaming an entity is ordinary, and it changes the id. Nothing about the
    // id can be relied on.
    expect(isStatusEntity(ours("sensor.is_my_solar_ok"))).toBe(true);
  });

  it("ignores every other integration's status sensor", () => {
    // The regression this file exists for. A real installation had seven of
    // these, and a card that matched `_status` in the id told its owner they
    // had more than one Solar Sanity.
    for (const id of [
      "sensor.frigate_status",
      "sensor.alarmo_status",
      "sensor.omada_gateway_status",
      "sensor.tigo_system_status",
      "sensor.siseli_inverter_1_status",
      "sensor.robovac_status",
      "sensor.hisense_ac_status",
    ]) {
      expect(isStatusEntity(entity(id, "online"))).toBe(false);
    }
  });

  it("ignores an enum sensor with a different vocabulary", () => {
    expect(isStatusEntity(entity("sensor.x", "on", { options: ["on", "off"] }))).toBe(false);
  });

  it("ignores a partial overlap", () => {
    expect(isStatusEntity(entity("sensor.x", "ok", { options: ["ok", "investigating"] }))).toBe(
      false,
    );
  });

  it("survives an entity with no attributes at all", () => {
    expect(isStatusEntity(entity("sensor.x", "unknown"))).toBe(false);
    expect(isStatusEntity(undefined)).toBe(false);
  });
});

describe("verdictFor", () => {
  it("says nothing alarming when the data checks out", () => {
    const verdict = verdictFor("ok", { days_of_data: 30 });

    expect(verdict.glyph).toBe("ok");
    expect(verdict.headline).toBe("Data checks out");
  });

  it("distinguishes present-but-quiet from not installed", () => {
    // Telling somebody to install what they already have sends them to add a
    // second copy, which is its own mess.
    expect(verdictFor("unavailable", {}).headline).toBe("Not answering right now");
    expect(verdictFor(undefined, {}).headline).toBe("Solar Sanity is not set up yet");
  });

  it("offers a way forward only when there is one", () => {
    expect(verdictFor("unavailable", {}).action).toBeUndefined();
    expect(verdictFor(undefined, {}).action).toBeDefined();
  });
});

describe("the card", () => {
  let card: SolarSanityCard;

  const hass = (states: HassEntity[]) =>
    ({
      states: Object.fromEntries(states.map((s) => [s.entity_id, s])),
      config: { state: "RUNNING" },
      language: "en",
    }) as unknown as HomeAssistant;

  beforeEach(() => {
    card = document.createElement("solar-sanity-card") as SolarSanityCard;
    card.setConfig({ type: "custom:solar-sanity-card" });
    document.body.append(card);
  });

  const text = async () => {
    await card.updateComplete;
    return card.shadowRoot?.textContent?.replace(/\s+/g, " ").trim() ?? "";
  };

  it("finds the one installation among a houseful of other sensors", async () => {
    card.hass = hass([
      entity("sensor.frigate_status", "online"),
      entity("sensor.alarmo_status", "disarmed"),
      ours(),
      entity("sensor.omada_gateway_status", "connected"),
    ]);

    expect(await text()).toContain("Data checks out");
  });

  it("says so when there really are two", async () => {
    card.hass = hass([ours("sensor.solar_sanity_status"), ours("sensor.solar_sanity_status_2")]);

    expect(await text()).toContain("More than one installation");
  });

  it("takes the configured entity over the search", async () => {
    card.setConfig({ type: "custom:solar-sanity-card", entity: "sensor.b" });
    card.hass = hass([ours("sensor.a"), ours("sensor.b", "fault_found")]);

    expect(await text()).toContain("does not add up");
  });
});


describe("leadSentence", () => {
  /**
   * Verbatim from `analysis/faults.py`, rendered. These are the actual longest
   * things this card is ever asked to show, and the reason it needed a lead at
   * all: the card is pinned to three rows and cannot grow.
   */
  const REAL = {
    signedNetBattery:
      "Battery charging goes negative for part of most days. A sensor mapped to one battery direction should only ever report that direction; one that swings both ways is a net figure, so charging and discharging cancel each other out inside a single channel and neither is counted properly.",
    unitScale:
      "Grid import reports values around 1234.5 while the rest of your system sits near 1.2. That is the signature of a sensor publishing one unit while declaring another — watts labelled as kilowatts, or watt-hours labelled as kilowatt-hours.",
    signInverted:
      "When Battery charging reads a positive number, the energy is actually flowing the other way. Every hour it is counted on the wrong side of the sum, which is why the total misses by twice its size.",
  };

  it("takes the observation and leaves the explanation", () => {
    expect(leadSentence(REAL.signedNetBattery)).toBe(
      "Battery charging goes negative for part of most days.",
    );
  });

  it("does not split a decimal number", () => {
    // "1234.5" and "1.2" both sit inside the first sentence of a real fault.
    expect(leadSentence(REAL.unitScale)).toContain("1234.5");
    expect(leadSentence(REAL.unitScale)).toContain("1.2");
  });

  it("keeps every real fault within the space the card has", () => {
    for (const [name, detail] of Object.entries(REAL)) {
      expect(leadSentence(detail).length, name).toBeLessThanOrEqual(131);
    }
  });

  it("truncates at a word, not mid-word, when a sentence is itself long", () => {
    const long = `${"word ".repeat(40)}end.`;
    const lead = leadSentence(long);

    expect(lead.endsWith("…")).toBe(true);
    expect(lead).not.toMatch(/wo…$/);
  });

  it("leaves a short body exactly as it was", () => {
    expect(leadSentence("Everything reconciles across 30 days of data.")).toBe(
      "Everything reconciles across 30 days of data.",
    );
  });

  it("survives nothing at all", () => {
    expect(leadSentence("")).toBe("");
    expect(leadSentence("   ")).toBe("");
  });

  it("returns text with no sentence end unchanged when it fits", () => {
    expect(leadSentence("no full stop here")).toBe("no full stop here");
  });
});

describe("a fault on a card that cannot grow", () => {
  const DETAIL =
    "Battery charging goes negative for part of most days. A sensor mapped to one battery direction should only ever report that direction; one that swings both ways is a net figure, so charging and discharging cancel each other out inside a single channel and neither is counted properly.";

  it("shows the lead, not the whole thing", () => {
    const verdict = verdictFor("fault_found", {
      headline: "Battery charging measures both directions at once",
      detail: DETAIL,
    });

    expect(verdict.body).toBe("Battery charging goes negative for part of most days.");
    expect(verdict.body.length).toBeLessThan(DETAIL.length);
  });

  it("never leaves the rest unreachable", () => {
    // The abridged text is only acceptable because the whole of it is one
    // click away, and carried on the element for a hover as well.
    const verdict = verdictFor("fault_found", { detail: DETAIL });

    expect(verdict.full).toBe(DETAIL);
    expect(verdict.action?.href).toBe("/config/repairs");
  });

  it("puts the whole text on the element", async () => {
    const card = document.createElement("solar-sanity-card") as SolarSanityCard;
    card.setConfig({ type: "custom:solar-sanity-card" });
    document.body.append(card);
    card.hass = {
      states: {
        "sensor.solar_sanity_status": {
          entity_id: "sensor.solar_sanity_status",
          state: "fault_found",
          attributes: {
            options: ["ok", "insufficient_data", "not_checkable", "investigating", "fault_found"],
            detail: DETAIL,
          },
          last_updated: "",
        },
      },
      config: { state: "RUNNING" },
      language: "en",
    } as never;
    await card.updateComplete;

    expect(card.shadowRoot?.querySelector(".body")?.getAttribute("title")).toBe(DETAIL);
  });
});
