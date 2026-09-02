/**
 * The editors, driven as the dashboard drives them.
 *
 * These are rendered rather than unit-tested around, because everything worth
 * getting wrong here is in the wiring: whether the emitted event escapes the
 * shadow root, whether a cleared field leaves `null` behind in the YAML, and
 * whether the schema offers the two entities that matter or the four hundred
 * that do not. None of that is visible from a pure function.
 *
 * `<ha-form>` is Home Assistant's, so it is not defined here. An undefined
 * custom element renders as an inert `HTMLElement` with the properties still
 * set on it, which is exactly enough to read the schema back off it.
 */

import { beforeEach, describe, expect, it } from "vitest";

import "./editors";
import type { SolarSanityCardEditor, SolarSanityForecastCardEditor } from "./editors";
import { SolarSanityCard } from "./status-card";
import { SolarSanityForecastCard } from "./forecast-card";
import type { HassEntity, HomeAssistant, LovelaceCardConfig } from "./types/hass";

const OPTIONS = ["ok", "insufficient_data", "not_checkable", "investigating", "fault_found"];

function ours(id: string): HassEntity {
  return { entity_id: id, state: "ok", attributes: { options: OPTIONS }, last_updated: "" };
}

function hassWith(entities: HassEntity[], archives: unknown[] = []): HomeAssistant {
  return {
    states: Object.fromEntries(entities.map((e) => [e.entity_id, e])),
    config: { state: "RUNNING" },
    themes: { darkMode: false },
    language: "en",
    translationMetadata: { translations: {} },
    callWS: async <T,>() => archives as T,
    callService: async () => undefined,
    localize: (key: string) => key,
  };
}

/** What `<ha-form>` was handed, once the editor has rendered. */
interface Form extends HTMLElement {
  schema?: readonly { name: string; selector: Record<string, unknown> }[];
  data?: Record<string, unknown>;
}

async function form(editor: HTMLElement): Promise<Form> {
  document.body.append(editor);
  await (editor as unknown as { updateComplete: Promise<unknown> }).updateComplete;
  const found = editor.shadowRoot!.querySelector("ha-form");
  expect(found, "the editor rendered no form").not.toBeNull();
  return found as Form;
}

/** Everything the editor rendered outside the form, as plain text. */
function note(editor: HTMLElement): string {
  return (editor.shadowRoot!.textContent ?? "").replace(/\s+/g, " ").trim();
}

/** Drive `<ha-form>`'s own event, which is the only way a value ever arrives. */
function change(editor: HTMLElement, value: Record<string, unknown>): LovelaceCardConfig {
  let emitted: LovelaceCardConfig | undefined;
  // On `document`, not on the editor: the point of the assertion is that the
  // event escapes the shadow root and reaches the dialog above it.
  const listen = (event: Event) => {
    emitted = (event as CustomEvent).detail.config;
  };
  document.addEventListener("config-changed", listen);
  editor
    .shadowRoot!.querySelector("ha-form")!
    .dispatchEvent(
      new CustomEvent("value-changed", { detail: { value }, bubbles: true, composed: true }),
    );
  document.removeEventListener("config-changed", listen);
  expect(emitted, "no config-changed reached the document").toBeDefined();
  return emitted!;
}

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("the cards hand back an editor", () => {
  it("both of them, and by tag name", () => {
    // The registration in `editors.ts` is what makes these real elements
    // rather than `HTMLUnknownElement`. Importing the module for side effects
    // is easy to lose in a tidy-up.
    expect(SolarSanityCard.getConfigElement().localName).toBe("solar-sanity-card-editor");
    expect(SolarSanityForecastCard.getConfigElement().localName).toBe(
      "solar-sanity-forecast-card-editor",
    );
  });
});

describe("the status card editor", () => {
  const make = (config: LovelaceCardConfig, hass: HomeAssistant) => {
    const editor = SolarSanityCard.getConfigElement() as SolarSanityCardEditor;
    editor.setConfig(config);
    editor.hass = hass;
    return editor;
  };

  const base = { type: "custom:solar-sanity-card" };

  it("offers only our own entities, not every sensor in the house", async () => {
    const hass = hassWith([
      ours("sensor.solar_sanity_status"),
      ours("sensor.shed_status"),
      { entity_id: "sensor.frigate_status", state: "on", attributes: {}, last_updated: "" },
      { entity_id: "sensor.power", state: "12", attributes: {}, last_updated: "" },
    ]);

    const rendered = await form(make(base, hass));
    const entityRow = rendered.schema!.find((row) => row.name === "entity")!;

    expect(entityRow.selector.entity).toEqual({
      include_entities: ["sensor.shed_status", "sensor.solar_sanity_status"],
    });
  });

  it("says which installation was picked when the field is empty", async () => {
    // The reason this editor exists. The card auto-selects and the interface
    // had nowhere that said which one it chose.
    const editor = make(base, hassWith([ours("sensor.solar_sanity_status")]));
    await form(editor);

    expect(note(editor)).toContain("sensor.solar_sanity_status");
    expect(note(editor)).toContain("found automatically");
  });

  it("says nothing about auto-selection once a choice has been made", async () => {
    const editor = make(
      { ...base, entity: "sensor.shed_status" },
      hassWith([ours("sensor.shed_status"), ours("sensor.solar_sanity_status")]),
    );
    await form(editor);

    expect(note(editor)).not.toContain("found automatically");
  });

  it("asks for a choice when the card would refuse to make one", async () => {
    const editor = make(
      base,
      hassWith([ours("sensor.a_status"), ours("sensor.b_status")]),
    );
    await form(editor);

    expect(note(editor)).toContain("2 installations");
  });

  it("does not call an empty house a broken one", async () => {
    const editor = make(base, hassWith([]));
    await form(editor);

    expect(note(editor)).toContain("will fill in by itself");
  });

  it("keeps the card type, which the form never sends", async () => {
    const editor = make(base, hassWith([ours("sensor.solar_sanity_status")]));
    await form(editor);

    expect(change(editor, { entity: "sensor.solar_sanity_status" })).toEqual({
      type: "custom:solar-sanity-card",
      entity: "sensor.solar_sanity_status",
    });
  });

  it("removes a cleared field rather than writing it as null", async () => {
    // `{entity: undefined}` serialises into a dashboard as `entity: null`, and
    // null is not absent: absent means "find your own", null means "look up an
    // entity called nothing" and renders an empty card.
    const editor = make({ ...base, entity: "sensor.a_status" }, hassWith([ours("sensor.a_status")]));
    await form(editor);

    const emitted = change(editor, { entity: undefined, entity_id: "" });

    expect(emitted).toEqual({ type: "custom:solar-sanity-card" });
    expect("entity" in emitted).toBe(false);
    expect("entity_id" in emitted).toBe(false);
  });

  it("offers nothing the card does not read", async () => {
    // The editor carried a Title row and a "Show the numbers behind the
    // verdict" switch for three releases, and the card read neither. Every
    // other test here reaches for one row with `.find`, so none of them could
    // see a row that should not exist. This compares the whole list.
    const rendered = await form(make(base, hassWith([ours("sensor.a_status")])));

    expect(rendered.schema!.map((row) => row.name)).toEqual(["entity"]);
  });
});

describe("the forecast card editor", () => {
  const archive = (id: string, name: string) => ({ statistic_id: id, name });

  const make = async (config: LovelaceCardConfig, archives: unknown[]) => {
    const editor = SolarSanityForecastCard.getConfigElement() as SolarSanityForecastCardEditor;
    editor.setConfig(config);
    editor.hass = hassWith([], archives);
    const rendered = await form(editor);
    // The archive list arrives from a WebSocket call, so the first render is
    // always the empty one. Wait for the state it sets.
    await (editor as unknown as { updateComplete: Promise<unknown> }).updateComplete;
    return { editor, rendered };
  };

  const base = { type: "custom:solar-sanity-forecast-card" };

  it("lists the providers actually archiving, by their readable names", async () => {
    const { editor } = await make(base, [
      archive("solar_sanity:dayahead_b", "Forecast.Solar forecast, a day ahead"),
      archive("solar_sanity:dayahead_a", "Solcast forecast, a day ahead"),
      // Not a day-ahead archive. It is ours, but it is a measurement series.
      archive("solar_sanity:measured_x_pv", "PV"),
      archive("sensor.other_integration", "Something else"),
    ]);
    const rendered = editor.shadowRoot!.querySelector("ha-form") as Form;
    const row = rendered.schema!.find((r) => r.name === "provider")!;

    expect((row.selector.select as { options: unknown[] }).options).toEqual([
      { value: "solar_sanity:dayahead_a", label: "Solcast" },
      { value: "solar_sanity:dayahead_b", label: "Forecast.Solar" },
    ]);
  });

  it("falls back to free text when nothing is archiving yet", async () => {
    // A dropdown with no options is a dead end. Free text at least lets
    // somebody paste an id they know.
    const { editor } = await make(base, []);
    const rendered = editor.shadowRoot!.querySelector("ha-form") as Form;

    expect(rendered.schema).toEqual([{ name: "provider", selector: { text: {} } }]);
    expect(note(editor)).toContain("No day-ahead archive");
  });

  it("does not turn a recorder failure into a broken editor", async () => {
    const editor = SolarSanityForecastCard.getConfigElement() as SolarSanityForecastCardEditor;
    editor.setConfig(base);
    editor.hass = {
      ...hassWith([]),
      callWS: async () => {
        throw new Error("recorder is disabled");
      },
    };
    const rendered = await form(editor);
    await (editor as unknown as { updateComplete: Promise<unknown> }).updateComplete;

    // The free-text fallback specifically, not merely "a schema" — that would
    // pass without the `catch` too, since the first render is empty anyway.
    expect(rendered.schema).toEqual([{ name: "provider", selector: { text: {} } }]);
    expect(note(editor)).toContain("No day-ahead archive");
  });

  it("says it is showing all of them when none is chosen", async () => {
    const { editor } = await make(base, [
      archive("solar_sanity:dayahead_a", "Solcast forecast, a day ahead"),
      archive("solar_sanity:dayahead_b", "Forecast.Solar forecast, a day ahead"),
    ]);

    expect(note(editor)).toContain("all 2 providers");
  });

  it("keeps the card type on the way out", async () => {
    const { editor } = await make(base, [
      archive("solar_sanity:dayahead_a", "Solcast forecast, a day ahead"),
    ]);

    expect(change(editor, { provider: "solar_sanity:dayahead_a" })).toEqual({
      type: "custom:solar-sanity-forecast-card",
      provider: "solar_sanity:dayahead_a",
    });
  });
});
