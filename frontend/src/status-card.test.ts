import { beforeEach, describe, expect, it } from "vitest";

import { isStatusEntity, SolarSanityCard, verdictFor } from "./status-card";
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
