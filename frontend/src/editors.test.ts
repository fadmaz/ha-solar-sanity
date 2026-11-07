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
import type { SolarSanityBatteryCardEditor, SolarSanityForecastCardEditor } from "./editors";
import { SolarSanityBatteryCard } from "./battery-card";
import { SolarSanityForecastCard } from "./forecast-card";
import type { HassEntity, HomeAssistant, LovelaceCardConfig } from "./types/hass";

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
    expect(SolarSanityBatteryCard.getConfigElement().localName).toBe(
      "solar-sanity-battery-card-editor",
    );
    expect(SolarSanityForecastCard.getConfigElement().localName).toBe(
      "solar-sanity-forecast-card-editor",
    );
  });
});

describe("the battery card editor", () => {
  const make = (config: LovelaceCardConfig, entities: Record<string, unknown> = {}) => {
    const editor = SolarSanityBatteryCard.getConfigElement() as SolarSanityBatteryCardEditor;
    editor.setConfig(config);
    editor.hass = { ...hassWith([]), entities } as HomeAssistant;
    return editor;
  };
  const outlook = (device: string) => ({
    [`sensor.lowest_${device}`]: {
      entity_id: `sensor.lowest_${device}`,
      platform: "solar_sanity",
      translation_key: "battery_lowest_before_sunrise",
      device_id: device,
    },
  });

  it("offers Home Assistant's own device picker, filtered to this integration", async () => {
    const rendered = await form(make({ type: "custom:solar-sanity-battery-card" }));

    expect(rendered.schema?.map((row) => row.name)).toEqual(["device_id"]);
    expect(rendered.schema?.[0]?.selector).toEqual({ device: { integration: "solar_sanity" } });
  });

  it("says which installation it found when there is one", async () => {
    const editor = make({ type: "custom:solar-sanity-battery-card" }, outlook("dev-a"));
    await form(editor);

    expect(note(editor)).toContain("found automatically");
  });

  it("asks for a choice when there are two", async () => {
    const editor = make(
      { type: "custom:solar-sanity-battery-card" },
      { ...outlook("dev-a"), ...outlook("dev-b") },
    );
    await form(editor);

    expect(note(editor)).toContain("Choose one");
  });

  it("removes a cleared field rather than writing it as null", async () => {
    const editor = make({ type: "custom:solar-sanity-battery-card", device_id: "dev-a" });
    await form(editor);

    const emitted = change(editor, { device_id: undefined });

    expect(emitted).toEqual({ type: "custom:solar-sanity-battery-card" });
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
