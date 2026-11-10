import { afterEach, describe, expect, it } from "vitest";
import { render } from "lit";
import { qualityPanel } from "./quality";
import { SolarSanityBatteryCard } from "./battery-card";
import { SolarSanityForecastCard } from "./forecast-card";
import type { HomeAssistant } from "./types/hass";
import "./editors";

const hass = (): HomeAssistant => ({language: "ar", states: {}, entities: {},
  config: {state: "RUNNING", time_zone: "Asia/Jerusalem"}, callWS: async () => [],
} as unknown as HomeAssistant);

afterEach(() => { document.body.innerHTML = ""; });

describe("the English-only product contract", () => {
  it.each([SolarSanityBatteryCard, SolarSanityForecastCard])("keeps card copy English despite a legacy language override", async (Card) => {
    // A briefly published language option must not retain translated output.
    const card = new Card();
    card.setConfig({type: "custom:test", language: "ar"});
    document.body.append(card);
    await card.updateComplete;
    expect(card.shadowRoot!.textContent).toContain("Waiting for Home Assistant");
    expect(card.shadowRoot!.querySelector(".root")?.getAttribute("lang")).toBe("en");
    expect(card.shadowRoot!.querySelector(".root")?.getAttribute("dir")).toBe("ltr");
  });

  it.each(["solar-sanity-battery-card-editor", "solar-sanity-forecast-card-editor"])("offers no language selector in %s", async (tag) => {
    const editor = document.createElement(tag) as HTMLElement & {hass: HomeAssistant; updateComplete: Promise<unknown>};
    editor.hass = hass(); document.body.append(editor); await editor.updateComplete;
    const form = editor.shadowRoot!.querySelector("ha-form") as unknown as HTMLElement & {
      schema: {name: string}[]; computeLabel: (row: {name: string}) => string;
    };
    expect(form.schema.map((row) => row.name)).not.toContain("language");
    expect(form.computeLabel(form.schema[0]!)).toMatch(/Installation|Forecast provider/);
    let saved: Record<string, unknown> | undefined;
    editor.addEventListener("config-changed", (event) => { saved = (event as CustomEvent).detail.config; });
    form.dispatchEvent(new CustomEvent("value-changed", {detail: {value: {language: "ar"}}}));
    expect(saved).not.toHaveProperty("language");
  });

  it("keeps quality metrics English and unknown until observations exist", () => {
    const data = hass();
    const add = (key: string, state: string, attributes: Record<string, unknown>) => {
      const id = `sensor.${key}`;
      data.entities![id] = {entity_id: id, device_id: "home", platform: "solar_sanity", translation_key: key};
      data.states[id] = {entity_id: id, state, attributes, last_updated: ""};
    };
    add("collection_status", "ok", {});
    add("night_lowest_mae", "unknown", {sample_count: 0, excluded: {missing_soc: 2}, pending: 1});
    add("morning_fill_accuracy", "100", {sample_count: 1});
    const target = document.createElement("div"); document.body.append(target);
    render(qualityPanel(data, "home"), target);
    expect(target.textContent).toContain("Accuracy and data quality");
    expect(target.textContent).toContain("2 excluded · 1 pending");
    expect(target.querySelector("dd")!.textContent).toContain("—");
    expect(target.textContent).toContain("100.0 %");
    expect(target.querySelector("details")?.open).toBe(false);
  });
});
