/** Issued observations, read on demand from the same journal that grades them. */
import { LitElement, css, html, nothing, type TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import { findInstallations, hasOutlook } from "./battery-model";
import { safeTimeZone } from "./forecast-data";
import type { GridOptions, HomeAssistant, LovelaceCardConfig } from "./types/hass";

export interface HistoryRow {
  id: string; local_date: string; checkpoint: "morning" | "evening";
  issued_at: string | null; scheduled_at: string | null; until: string | null;
  time_zone: string | null; model_revision: string | null; previous_setup: boolean;
  settings: {capacity_kwh: number | null; reserve_pct: number | null; target_pct: number | null};
  published: {lowest_pct: number | null; reserve_at: string | null; full_at: string | null};
  result: {status: string; reason_code?: string | null; actual_lowest: number | null; error: number | null;
    reserve_correct: boolean | null; actual_filled: boolean | null; actual_full_at: string | null;
    fill_correct: boolean | null; timing_resolution: string | null; time_error_minutes: number | null};
  reason: string | null;
}
export interface HistoryResponse {time_zone: string; status: string; reason_code: string | null; records: HistoryRow[]}
export interface ToolsCardConfig extends LovelaceCardConfig {device_id?: string; default_tab?: string}

@customElement("solar-sanity-tools-card")
export class SolarSanityToolsCard extends LitElement {
  @property({attribute: false}) public hass?: HomeAssistant;
  @state() protected _config: ToolsCardConfig = {type: "custom:solar-sanity-tools-card"};
  @state() protected _days = 28;
  @state() protected _visibleDays = 7;
  @state() protected _history?: HistoryResponse;
  @state() protected _loading = false;
  @state() protected _error = "";
  private _key = "";
  private _generation = 0;

  public setConfig(config: ToolsCardConfig): void {
    if (config.device_id !== undefined && typeof config.device_id !== "string") throw new Error("Select an installation.");
    this._config = config;
  }
  public getCardSize(): number { return 8; }
  public getGridOptions(): GridOptions { return {rows: "auto", columns: 12, min_columns: 6}; }
  public static getConfigElement(): HTMLElement { return document.createElement("solar-sanity-tools-card-editor"); }
  public static getStubConfig(): ToolsCardConfig { return {type: "custom:solar-sanity-tools-card", default_tab: "history"}; }
  public override connectedCallback(): void { super.connectedCallback(); this.requestUpdate(); }
  public override disconnectedCallback(): void { super.disconnectedCallback(); this._generation++; this._key = ""; }

  protected _device(): string | undefined {
    const all = findInstallations(this.hass).filter(hasOutlook);
    return this._config.device_id ? all.find(i => i.deviceId === this._config.device_id)?.deviceId
      : all.length === 1 ? all[0]!.deviceId : undefined;
  }
  protected updated(): void {
    const device = this._device(), hass = this.hass;
    if (!device || !hass || hass.config.state !== "RUNNING") {
      if (this._key) { this._key = ""; this._generation++; this._history = undefined; this._loading = false; }
      return;
    }
    const metrics = Object.values(hass.entities ?? {}).filter(e => e.device_id === device
      && e.platform === "solar_sanity" && ["night_lowest_mae", "morning_fill_accuracy"].includes(e.translation_key ?? ""))
      .map(e => { const a = hass.states[e.entity_id]?.attributes; return [a?.sample_count, a?.pending, a?.excluded]; });
    const outlook = Object.values(hass.entities ?? {}).find(e => e.device_id === device && e.platform === "solar_sanity" && e.translation_key === "battery_lowest_before_sunrise");
    const key = JSON.stringify([device, this._days, metrics, hass.config.time_zone, outlook ? hass.states[outlook.entity_id]?.attributes.computed_at : null]);
    if (this._key !== key) { this._key = key; void this._load(device); }
  }
  private async _load(device: string): Promise<void> {
    const generation = ++this._generation;
    this._loading = true; this._error = ""; this._history = undefined; this._visibleDays = 7;
    try {
      const result = await this.hass!.callWS<{response: HistoryResponse}>({type: "call_service", domain: "solar_sanity",
        service: "get_forecast_history", service_data: {device_id: device, days: this._days}, return_response: true});
      if (generation !== this._generation || !this.isConnected) return;
      if (!result.response || !Array.isArray(result.response.records)) throw new Error("Invalid response");
      this._history = result.response;
    } catch {
      if (generation === this._generation && this.isConnected) this._error = "History could not be read. Check that Solar Sanity is loaded, then retry.";
    } finally { if (generation === this._generation) this._loading = false; }
  }
  private _retry(): void { this._key = ""; this.requestUpdate(); }
  protected _time(value: string | null, zone?: string | null): string {
    if (!value) return "—";
    const when = new Date(value);
    return Number.isNaN(when.getTime()) ? "—" : new Intl.DateTimeFormat("en", {
      timeZone: safeTimeZone(zone ?? this.hass?.config.time_zone), month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).format(when);
  }
  private _forecast(row: HistoryRow): string {
    if (!row.issued_at) return "No prediction issued";
    if (["no_forecast", "no_night"].includes(row.result.reason_code ?? "")) return "Prediction unavailable";
    const p = row.published;
    return row.checkpoint === "evening" ? (p.lowest_pct === null ? "—" : `Minimum ${p.lowest_pct.toFixed(1)}% · Reserve ${p.reserve_at ? this._time(p.reserve_at, row.time_zone) : "not reached"}`)
      : p.full_at ? `Full at ${this._time(p.full_at, row.time_zone)}` : "Not expected to fill";
  }
  private _observed(row: HistoryRow): string {
    const r = row.result;
    if (r.status !== "graded") return "—";
    if (row.checkpoint === "evening") {
      const reached = r.reserve_correct ? Boolean(row.published.reserve_at) : !row.published.reserve_at;
      return r.actual_lowest === null ? "—" : `Minimum ${r.actual_lowest.toFixed(1)}% · Reserve ${reached ? "reached" : "not reached"}`;
    }
    if (!r.actual_filled) return "Did not fill";
    return r.actual_full_at && r.timing_resolution === "state" ? `Full at ${this._time(r.actual_full_at, row.time_zone)}` : "Reached full · hourly evidence";
  }
  private _outcome(row: HistoryRow): TemplateResult {
    const r = row.result;
    return html`<span class="status">${({pending: "Pending", graded: "Graded", excluded: "Excluded", missed: "Missed"} as Record<string, string>)[r.status] ?? "Unknown"}</span>
      ${r.status === "graded" ? html`<span>${row.checkpoint === "evening"
        ? `Reserve call ${r.reserve_correct ? "correct" : "incorrect"} · Error ${r.error === null ? "—" : `${r.error.toFixed(1)} pp (predicted − observed)`}`
        : `Fill call ${r.fill_correct ? "correct" : "incorrect"}`}</span>
        ${r.time_error_minutes !== null && r.timing_resolution === "state" ? html`<span>Timing error ${Math.abs(r.time_error_minutes).toFixed(0)} min ${r.time_error_minutes < 0 ? "early" : "late"}</span>` : nothing}` : nothing}
      ${row.reason ? html`<span>${row.reason}</span>` : nothing}`;
  }
  protected _historyPanel(): TemplateResult {
    const history = this._history;
    const dates = [...new Set(history?.records.map(r => r.local_date) ?? [])];
    const visible = new Set(dates.slice(this._visibleDays - 7, this._visibleDays));
    return html`<div class="toolbar"><label>Period <select aria-label="History period" .value=${String(this._days)} @change=${(e: Event) => {this._days = Number((e.target as HTMLSelectElement).value);}}>
      ${[7, 28, 90].map(d => html`<option value=${d} ?selected=${d === this._days}>Last ${d} days</option>`)}</select></label>
      <button @click=${this._retry} ?disabled=${this._loading}>${this._error ? "Retry" : "Refresh"}</button></div>
      ${this._loading ? html`<p role="status">Loading issued predictions…</p>` : nothing}
      ${this._error ? html`<p role="alert">${this._error}</p>` : nothing}
      ${history?.status === "error" ? html`<p role="alert">The journal could not be read or saved. Available records may not be durable. Check Solar Sanity diagnostics.</p>` : nothing}
      ${history && history.records.length === 0 && history.status !== "error" ? html`<p>${history.status === "starting" ? "No issued predictions yet. Checkpoints are at 08:00 and 20:00 in Home Assistant's time zone." : "No predictions in the selected period. Choose a longer period."}</p>` : nothing}
      ${history?.records.length ? html`<p class="muted">Forecast vs actual · Error is predicted minus observed.</p>
        <div class="history">${history.records.filter(r => visible.has(r.local_date)).map(row => html`
          <article><header><strong>${row.local_date}</strong><span>${row.checkpoint === "evening" ? "20:00 · Night" : "08:00 · Morning"}</span></header>
            <dl><div><dt>Forecast</dt><dd>${this._forecast(row)}</dd></div><div><dt>Observed</dt><dd>${this._observed(row)}</dd></div>
            <div class="outcome"><dt>Result</dt><dd>${this._outcome(row)}</dd></div></dl>
            <p class="muted">${row.issued_at ? `Issued ${this._time(row.issued_at, row.time_zone)}` : "Checkpoint missed"}
              · ${row.model_revision ?? "Revision unavailable"}${row.time_zone ? ` · ${row.time_zone}` : ""}${row.previous_setup ? " · Previous setup" : ""}</p>
          </article>`)}</div>
        <div class="toolbar">${this._visibleDays > 7 ? html`<button @click=${() => {this._visibleDays -= 7;}}>Newer days</button>` : nothing}
        ${dates.length > this._visibleDays ? html`<button @click=${() => {this._visibleDays += 7;}}>Show earlier days</button>` : nothing}</div>` : nothing}`;
  }
  protected render(): TemplateResult {
    const note = !this.hass ? "Waiting for Home Assistant." : this.hass.config.state !== "RUNNING" ? "Home Assistant is still starting."
      : !this._device() ? "Choose an installation with a battery outlook in the card editor." : null;
    return html`<ha-card><div class="root" lang="en" dir="ltr"><h2>Solar Sanity tools</h2>
      ${note ? html`<p>${note}</p>` : html`<h3>History</h3>${this._historyPanel()}`}</div></ha-card>`;
  }
  public static styles = css`
    :host {display:block;color:var(--primary-text-color);font-family:var(--ha-card-header-font-family,inherit)}
    .root {padding:20px} h2 {font-size:1.2rem;font-weight:500;margin:0 0 20px} h3 {font-size:1rem;margin:0 0 12px}
    p {line-height:1.6} .muted,dt {color:var(--secondary-text-color);font-size:.875rem}
    .toolbar {display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:16px}
    button,select {font:inherit;color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:6px;padding:8px 12px;cursor:pointer}
    button:disabled {cursor:wait;opacity:.6} button:hover {background:var(--secondary-background-color)}
    button:focus-visible,select:focus-visible {outline:2px solid var(--primary-color);outline-offset:3px}
    article {border-top:1px solid var(--divider-color);padding:16px 0} article header {display:flex;gap:12px;justify-content:space-between;flex-wrap:wrap}
    dl {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin:16px 0} dt {margin-bottom:4px} dd {margin:0;line-height:1.5;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
    .outcome {grid-column:1/-1} .outcome dd {display:flex;gap:4px 12px;flex-wrap:wrap} .status {font-weight:500}
    ::selection {background:var(--primary-color);color:var(--text-primary-color,#fff)}
    @media(max-width:420px){.root{padding:16px}dl{grid-template-columns:1fr}.outcome{grid-column:auto}}
  `;
}
