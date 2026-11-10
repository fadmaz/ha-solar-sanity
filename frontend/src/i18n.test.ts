import { afterEach, describe, expect, it } from "vitest";
import { render } from "lit";
import { languageOf, number, reasonText, t } from "./i18n";
import { qualityPanel } from "./quality";
import { SolarSanityBatteryCard } from "./battery-card";
import { SolarSanityForecastCard } from "./forecast-card";
import type { HomeAssistant } from "./types/hass";
import "./editors";

const hass = (language = "ar"): HomeAssistant => ({language, states: {}, entities: {},
  config: {state: "RUNNING", time_zone: "Asia/Jerusalem"}, callWS: async () => [],
} as unknown as HomeAssistant);

afterEach(() => { document.body.innerHTML = ""; });

describe("the shared language contract", () => {
  it("follows Home Assistant and accepts an explicit card override", () => {
    expect(languageOf("ar-JO", "auto")).toBe("ar");
    expect(languageOf("ar", "en")).toBe("en");
    expect(languageOf("fr")).toBe("en");
    expect(t("ar", "{n} nights · {m} mornings", {n: 2, m: 3})).toBe("2 ليلة · 3 صباح");
    expect(number(12.5, "ar", 1)).toBe("١٢٫٥");
    expect(reasonText("ar", "forecast_stale", "legacy sentence")).toContain("ثلاث ساعات");
    expect(reasonText("ar", "unknown_future_code", "legacy sentence")).toBe("legacy sentence");
  });

  it.each([SolarSanityBatteryCard, SolarSanityForecastCard])("translates empty states and isolates card direction", async (Card) => {
    const card = new Card();
    card.setConfig({type: "custom:test", language: "ar"});
    document.body.append(card);
    await card.updateComplete;
    expect(card.shadowRoot!.textContent).toContain("بانتظار Home Assistant");
    expect(card.shadowRoot!.querySelector(".root")?.getAttribute("dir")).toBe("rtl");
    card.setConfig({type: "custom:test", language: "en"});
    await card.updateComplete;
    expect(card.shadowRoot!.textContent).toContain("Waiting for Home Assistant");
    expect(() => card.setConfig({type: "custom:test", language: "de" as never})).toThrow();
  });

  it.each(["solar-sanity-battery-card-editor", "solar-sanity-forecast-card-editor"])("keeps the language selector available before discovery in %s", async (tag) => {
    const editor = document.createElement(tag) as HTMLElement & {hass: HomeAssistant; updateComplete: Promise<unknown>};
    editor.hass = hass(); document.body.append(editor); await editor.updateComplete;
    const form = editor.shadowRoot!.querySelector("ha-form") as unknown as HTMLElement & {
      schema: {name: string}[]; computeLabel: (row: {name: string}) => string;
    };
    expect(form.schema.map((row) => row.name)).toContain("language");
    expect(form.computeLabel({name: "language"})).toBe("اللغة");
    expect(editor.shadowRoot!.querySelector("[dir]")?.getAttribute("dir")).toBe("rtl");
  });

  it("shows unknown metrics and actual sample counts without turning learning into an error", () => {
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
    render(qualityPanel(data, "ar", "home"), target);
    expect(target.textContent).toContain("الدقة وجودة البيانات");
    expect(target.textContent).toContain("التجميع سليم");
    expect(target.textContent).toContain("٢ مستبعدة · ١ قيد الانتظار");
    expect(target.querySelector("dd")!.textContent).toContain("—");
    expect(target.textContent).toContain("١٠٠٫٠ %");
    expect(target.querySelector("details")?.open).toBe(false);
  });
});
