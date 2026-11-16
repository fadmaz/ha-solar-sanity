/** Read-only setup details for the selected Solar Sanity installation. */
import { LitElement, css, html, nothing, type TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import { safeTimeZone } from "./forecast-data";
import { powerControlLabel, type BatteryControls } from "./battery-controls";
import type { HomeAssistant } from "./types/hass";
import type { LoadActionAvailability, LoadPlanning } from "./simulation";

type Status = "ready" | "waiting_for_data" | "needs_attention" | "not_configured";
type Action = "configure" | "reconfigure";
interface Check {status: Status; reason: string | null; action: Action | null}
interface Channel extends Check {key: string; kind: "power" | "energy" | null; unit: string | null; hours: number}
interface Provider extends Check {
  name?: string;
  integration: string | null;
  captured_at: string | null;
  attempted_at?: string | null;
  submitted_at?: string | null;
  provider_updated_at?: string | null;
  verified_at: string | null;
  verification: "pending" | "verified" | "unverified" | null;
  archive_series?: Partial<Record<"latest" | "dayahead", "pending" | "verified" | "unverified" | "not_required">> | null;
  evidence?: {
    status: "battery" | "ready" | "waiting";
    reason: string;
    factor: number | null;
    factor_source: string | null;
    comparable_days: number;
    training_days: number;
    validation_days: number;
    compared_hours: number;
    hourly_error_pct: number | null;
    exclusions: Record<string, number>;
  };
}
interface ForecastSource {
  provider: string | null;
  status: "auto" | "preferred" | "fallback" | "history" | "unavailable";
  captured_at: string | null;
  reason: string | null;
}
interface AutomationSignal {
  state: "on" | "off" | "unavailable";
  reason: string;
}
export interface SetupCheckResponse {
  time_zone: string;
  planning?: Check & LoadPlanning;
  measurements: Check & {channels: Channel[]};
  forecast: Check & {
    providers: Provider[];
    corrected: boolean;
    correction_reason: string | null;
    preferred_provider?: string | null;
    export?: {
      mode: "unknown" | "unlimited" | "fixed";
      limit_w: number | null;
      curtailment: "confirmed" | "possible" | "not_reported" | "unknown";
      confirmed_hours: number;
    };
    sources?: Partial<Record<"battery" | "today" | "tomorrow", ForecastSource>>;
    load_actions?: LoadActionAvailability | null;
  };
  battery: Check & {
    settings: {capacity_kwh: number; reserve_pct: number; target_pct: number} | null;
    charge_level: Check | null;
    controls?: BatteryControls;
  };
  history: Check & {
    days: number;
    forecast_source?: string | null;
    counts: {graded_nights: number; graded_mornings: number; pending: number; excluded: number} | null;
    exclusions: {reason: string; count: number}[];
  };
  automation?: {
    reserve_risk: (AutomationSignal & {reserve_pct: number; lead_minutes: number}) | null;
    solar_surplus_ready: AutomationSignal & {minimum_w: number; hold_minutes: number};
  };
}

const STATUS_LABELS: Record<Status, string> = {
  ready: "Ready",
  waiting_for_data: "Waiting for data",
  needs_attention: "Needs attention",
  not_configured: "Not configured",
};
const CHANNEL_LABELS: Record<string, string> = {
  pv: "Solar generation",
  load: "House consumption",
  grid_import: "Grid import",
  grid_export: "Grid export",
  battery_charge: "Battery charge",
  battery_discharge: "Battery discharge",
};
const ACTION_LABELS = {configure: "Configure", reconfigure: "Reconfigure"};
const REFRESH_KEYS = new Set(["collection_status", "forecast_last_captured_at", "forecast_archive_verified_at",
  "reserve_risk", "solar_surplus_ready"]);
const SOURCE_KEYS = new Set(["battery_lowest_before_sunrise", "solar_energy_today_corrected", "expected_tomorrow_corrected"]);
const SOURCE_LABELS = {battery:"Battery outlook",today:"Today's corrected forecast",tomorrow:"Tomorrow's corrected forecast"};
const ARCHIVE_LABELS = {latest: "Latest forecast", dayahead: "Day-ahead forecast"};
const ARCHIVE_STATUS = {pending: "Pending", verified: "Verified", unverified: "Unverified", not_required: "No hours due"};
const EXPORT_LABELS = {unknown: "Unknown", unlimited: "No configured limit", fixed: "Fixed limit"};
const CURTAILMENT_LABELS = {confirmed: "Active reduction reported", possible: "Possible reduction near export limit",
  not_reported: "Reduction sensor is off", unknown: "Reduction cannot be determined"};
const FIT_EXCLUSIONS: Record<string, string> = {daylight_unavailable: "Daylight window unavailable",
  forecast_incomplete: "Incomplete forecast", measurement_incomplete: "Incomplete measurement",
  confirmed_curtailment: "Production reduction reported", curtailment_not_ruled_out: "Production reduction cannot be ruled out",
  low_forecast_energy: "Low forecast energy"};
const numbers = new Intl.NumberFormat("en", {maximumFractionDigits: 2});

@customElement("solar-sanity-setup-check")
export class SolarSanitySetupCheck extends LitElement {
  @property({attribute: false}) public hass?: HomeAssistant;
  @property() public deviceId = "";
  @property({type: Boolean}) public active = false;
  @state() private _data?: SetupCheckResponse;
  @state() private _loading = false;
  @state() private _error = false;
  private _key = "";
  private _generation = 0;

  public override connectedCallback(): void {
    super.connectedCallback();
    this.requestUpdate();
  }

  public override disconnectedCallback(): void {
    super.disconnectedCallback();
    this._reset();
  }

  private _reset(): void {
    this._generation++;
    this._key = "";
    this._data = undefined;
    this._loading = false;
    this._error = false;
  }

  protected override willUpdate(): void {
    const hass = this.hass, device = this.deviceId;
    if (!this.active || !this.isConnected || !device || hass?.config.state !== "RUNNING") {
      if (this._key) this._reset();
      return;
    }
    const health = Object.values(hass.entities ?? {})
      .filter(e => e.platform === "solar_sanity" && e.device_id === device
        && (REFRESH_KEYS.has(e.translation_key ?? "") || SOURCE_KEYS.has(e.translation_key ?? "")))
      .map(e => {
        const state = hass.states[e.entity_id];
        if (SOURCE_KEYS.has(e.translation_key ?? "")) {
          return [e.entity_id, state?.attributes.forecast_statistic_id, state?.attributes.production_from,
            state?.attributes.reason, !state || ["unknown", "unavailable"].includes(state.state)];
        }
        return [e.entity_id, state?.state, state?.attributes.reason_code, state?.attributes.verification,
          state?.attributes.reason, state?.attributes.lead_minutes, state?.attributes.minimum_w,
          state?.attributes.hold_minutes, state?.attributes.load_planning, state?.attributes.forecast_evidence_revision];
      });
    const key = JSON.stringify([device, hass.config.time_zone, health]);
    if (this._key === key) return;
    this._key = key;
    void this._load(hass, device);
  }

  private async _load(hass: HomeAssistant, device: string): Promise<void> {
    const generation = ++this._generation;
    this._data = undefined;
    this._loading = true;
    this._error = false;
    try {
      const {response} = await hass.callWS<{response?: SetupCheckResponse}>({
        type: "call_service",
        domain: "solar_sanity",
        service: "get_setup_check",
        service_data: {device_id: device},
        return_response: true,
      });
      if (generation !== this._generation) return;
      if (!response?.measurements || !response.forecast || !response.battery || !response.history) {
        throw new Error("Invalid setup check response");
      }
      this._data = response;
    } catch {
      if (generation === this._generation) this._error = true;
    } finally {
      if (generation === this._generation) this._loading = false;
    }
  }

  private _refresh(): void {
    this._key = "";
    this.requestUpdate();
  }

  private _time(value: string | null): string {
    if (!value) return "None yet";
    const when = new Date(value);
    if (Number.isNaN(when.getTime())) return "Unavailable";
    return new Intl.DateTimeFormat("en", {
      timeZone: safeTimeZone(this._data?.time_zone ?? this.hass?.config.time_zone),
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).format(when);
  }

  private _status(status: Status): TemplateResult {
    return html`<span class="status" data-status=${status}>${STATUS_LABELS[status]}</span>`;
  }

  private _details(check: Check): TemplateResult {
    return html`${check.reason ? html`<p>${check.reason}</p>` : nothing}
      ${check.action ? html`<p>Use <strong>${ACTION_LABELS[check.action]}</strong>.</p>` : nothing}`;
  }

  private _group(title: string, check: Check, content: unknown): TemplateResult {
    return html`<section aria-label=${title}><header><h3>${title}</h3>${this._status(check.status)}</header>
      ${this._details(check)}${content}</section>`;
  }

  private _measurements(data: SetupCheckResponse["measurements"]): TemplateResult {
    return this._group("Measurements", data, html`<ul>${data.channels.map(channel => html`
      <li><header><h4>${CHANNEL_LABELS[channel.key] ?? "Measurement"}</h4>${this._status(channel.status)}</header>
        <p>${channel.kind ? `${channel.kind === "power" ? "Power" : "Energy"}${channel.unit ? ` · ${channel.unit}` : ""} · ` : ""}
          ${numbers.format(channel.hours)} retained ${channel.hours === 1 ? "hour" : "hours"}</p>
        ${this._details(channel)}
      </li>`)}</ul>`);
  }

  private _forecast(data: SetupCheckResponse["forecast"]): TemplateResult {
    const grid = data.export;
    return this._group("Solar forecast", data, html`
      ${data.sources && data.providers.length ? html`<p>Preferred forecast provider <strong>${data.preferred_provider ?? "Auto"}</strong></p>` : nothing}
      ${data.load_actions?.reason ? html`<p>Load actions · ${data.load_actions.reason}</p>` : nothing}
      ${grid ? html`<dl><div><dt>Grid export</dt><dd>${EXPORT_LABELS[grid.mode]}${grid.limit_w === null ? "" : ` · ${numbers.format(grid.limit_w)} W`}</dd></div>
        <div><dt>Curtailment</dt><dd>${CURTAILMENT_LABELS[grid.curtailment]}</dd></div>
        ${grid.confirmed_hours ? html`<div><dt>Hours with reported reduction</dt><dd>${grid.confirmed_hours}</dd></div>` : nothing}
      </dl>` : nothing}
      <ul>
        ${data.sources ? (Object.keys(SOURCE_LABELS) as (keyof typeof SOURCE_LABELS)[]).map(key => {
          const source = data.sources?.[key];
          if (!source) return nothing;
          const label = source.status === "history" ? "Production history"
            : source.status === "fallback" ? "Using fallback"
              : source.status === "unavailable" ? "Unavailable" : null;
          return html`<li><header><h4>${SOURCE_LABELS[key]}</h4>
            ${label ? html`<span class="status">${label}</span>` : nothing}</header>
            ${source.provider ? html`<p>${source.provider}${source.captured_at ? ` · Captured ${this._time(source.captured_at)}` : ""}</p>` : nothing}
            ${source.reason ? html`<p>${source.reason}</p>` : nothing}</li>`;
        }) : data.providers.length ? html`
          <li><header><h4>Corrected forecast</h4>${this._status(data.corrected ? "ready" : "waiting_for_data")}</header>
            ${data.correction_reason ? html`<p>${data.correction_reason}</p>` : nothing}</li>` : nothing}
        ${data.providers.map((provider, index) => {
          const name = provider.integration === "forecast_solar" ? "Forecast.Solar"
            : provider.integration?.replaceAll("_", " ");
          const label = provider.name ?? (data.providers.length === 1 && name ? name
            : `Provider ${index + 1}${name ? ` · ${name}` : ""}`);
          const verification = provider.verification === "pending" ? "Verification pending"
            : provider.verification === "unverified" ? "Latest batch unverified" : null;
          return html`<li><header><h4>${label}</h4>${this._status(provider.status)}</header>
            <dl><div><dt>Last successful provider update</dt><dd>${this._time(provider.provider_updated_at ?? null)}</dd></div>
              <div><dt>Last capture attempt</dt><dd>${this._time(provider.attempted_at ?? null)}</dd></div>
              <div><dt>Last valid capture</dt><dd>${this._time(provider.captured_at)}</dd></div>
              <div><dt>Last submitted batch</dt><dd>${this._time(provider.submitted_at ?? null)}</dd></div>
              <div><dt>Last verified archive read-back</dt><dd>${this._time(provider.verified_at)}</dd></div></dl>
            ${verification ? html`<p>${verification}</p>` : nothing}
            ${provider.archive_series ? html`<dl>${(Object.keys(ARCHIVE_LABELS) as (keyof typeof ARCHIVE_LABELS)[]).map(key => {
              const status = provider.archive_series?.[key];
              return status ? html`<div><dt>${ARCHIVE_LABELS[key]}</dt><dd>${ARCHIVE_STATUS[status]}</dd></div>` : nothing;
            })}</dl>` : nothing}
            ${this._details(provider)}
            ${provider.evidence ? this._forecastEvidence(provider.evidence) : nothing}</li>`;
        })}
      </ul>`);
  }

  private _forecastEvidence(evidence: NonNullable<Provider["evidence"]>): TemplateResult {
    const factor = evidence.factor === null ? "—" : evidence.factor.toFixed(2);
    const error = evidence.hourly_error_pct === null ? "—" : `${numbers.format(evidence.hourly_error_pct)}%`;
    const excluded = Object.entries(evidence.exclusions);
    return html`<details><summary>Forecast evidence</summary>
      ${evidence.status === "ready" ? html`<p>Forecast source ready for manual planning.</p>` : nothing}
      <p>${evidence.reason}</p>
      <dl><div><dt>${evidence.status === "battery" ? "Tomorrow’s correction" : "Planning factor"}</dt><dd>${factor}</dd></div>
        <div><dt>Comparable days</dt><dd>${evidence.comparable_days}</dd></div>
        <div><dt>Training days</dt><dd>${evidence.training_days}</dd></div>
        <div><dt>Validation days</dt><dd>${evidence.validation_days}</dd></div>
        <div><dt>Validation hours</dt><dd>${evidence.compared_hours}</dd></div>
        <div><dt>Hourly validation error</dt><dd>${error}</dd></div>
      </dl>
      ${excluded.length ? html`<dl>${excluded.map(([key, count]) =>
        html`<div><dt>${FIT_EXCLUSIONS[key] ?? key}</dt><dd>${count} days excluded</dd></div>`)}</dl>` : nothing}
    </details>`;
  }

  private _battery(data: SetupCheckResponse["battery"]): TemplateResult {
    const settings = data.settings, chargeLevel = data.charge_level, controls = data.controls;
    return this._group("Battery outlook", data, settings ? html`
      <dl><div><dt>Usable capacity</dt><dd>${numbers.format(settings.capacity_kwh)} kWh</dd></div>
        <div><dt>Reserve</dt><dd>${numbers.format(settings.reserve_pct)}%</dd></div>
        <div><dt>Charge target</dt><dd>${numbers.format(settings.target_pct)}%</dd></div>
        ${controls ? html`
          <div><dt>Battery charge power limit</dt><dd>${powerControlLabel(controls.battery_charge_power_limit_w)}</dd></div>
          <div><dt>Inverter standby draw</dt><dd>${powerControlLabel(controls.inverter_standby_w)}</dd></div>` : nothing}
      </dl>
      ${chargeLevel ? html`<ul><li><header><h4>Charge level</h4>${this._status(chargeLevel.status)}</header>
        ${this._details(chargeLevel)}</li></ul>` : nothing}` : nothing);
  }

  private _history(data: SetupCheckResponse["history"]): TemplateResult {
    const counts = data.counts;
    return this._group("History and accuracy", data, counts ? html`
      <p>Last ${data.days} days in the current setup${data.forecast_source ? ` · ${data.forecast_source}` : ""}</p>
      <dl><div><dt>Graded nights</dt><dd>${counts.graded_nights}</dd></div>
        <div><dt>Graded mornings</dt><dd>${counts.graded_mornings}</dd></div>
        <div><dt>Pending</dt><dd>${counts.pending}</dd></div>
        <div><dt>Excluded or missed</dt><dd>${counts.excluded}</dd></div></dl>
      ${data.exclusions.length ? html`<ul aria-label="Exclusion reasons">
        ${data.exclusions.map(({count, reason}) => html`<li>${count} · ${reason}</li>`)}
      </ul>` : nothing}` : nothing);
  }

  private _automation(data: NonNullable<SetupCheckResponse["automation"]>): TemplateResult {
    const reserve = data.reserve_risk, surplus = data.solar_surplus_ready;
    const labels = {on: "On", off: "Off", unavailable: "Unavailable"};
    return html`<section aria-label="Automation signals">
      <h3>Automation signals</h3>
      <ul>
        ${reserve ? html`<li><header><h4>Reserve risk</h4><span class="status">${labels[reserve.state]}</span></header>
          <p>${numbers.format(reserve.reserve_pct)}% reserve · ${numbers.format(reserve.lead_minutes)} min warning</p>
          <p>${reserve.reason}</p></li>` : nothing}
        <li><header><h4>Solar surplus ready</h4><span class="status">${labels[surplus.state]}</span></header>
          <p>${numbers.format(surplus.minimum_w)} W minimum · ${numbers.format(surplus.hold_minutes)} min hold</p>
          <p>${surplus.reason}</p></li>
      </ul>
    </section>`;
  }

  private _planning(data: NonNullable<SetupCheckResponse["planning"]>): TemplateResult {
    return this._group("Load planning", data, data.mode === "battery" ? html`
      <dl><div><dt>Mode</dt><dd>Battery</dd></div>
        <div><dt>Minimum planned battery charge</dt><dd>${data.minimum_soc_pct == null ?
          `Use reserve (${numbers.format(data.reserve_pct)}%)` : `${numbers.format(data.minimum_soc_pct)}%`}</dd></div></dl>`
      : data.mode === "solar_only" ? html`
      <dl><div><dt>Mode</dt><dd>Grid-connected solar without a battery</dd></div>
        <div><dt>Maximum estimated additional grid energy</dt><dd>${data.max_extra_grid_kwh == null ?
          "No limit" : `${numbers.format(data.max_extra_grid_kwh)} kWh`}</dd></div></dl>` : nothing);
  }

  protected override render(): TemplateResult {
    const data = this._data;
    const needsSettings = data && [
      data.battery, data.battery.charge_level, data.forecast, data.planning,
      ...data.forecast.providers, ...data.measurements.channels,
    ].some(check => check?.action);
    return html`<div class="toolbar">
      <button @click=${this._refresh} ?disabled=${this._loading}>${this._error ? "Retry" : "Refresh setup check"}</button>
      ${this._loading ? html`<span role="status">Checking setup…</span>` : nothing}
    </div>
      ${this._error ? html`<p role="alert">Setup Check could not be read. Check that Solar Sanity is loaded and you can read this installation, then retry.</p>` : nothing}
      ${data ? html`<div class="groups">
        ${this._measurements(data.measurements)}
        ${this._forecast(data.forecast)}
        ${this._battery(data.battery)}
        ${data.planning ? this._planning(data.planning) : nothing}
        ${data.automation ? this._automation(data.automation) : nothing}
        ${this._history(data.history)}
      </div>` : nothing}
      ${needsSettings ? html`<p class="settings">Open <strong>Settings → Devices &amp; services → Solar Sanity</strong> and select this installation.</p>` : nothing}`;
  }

  public static styles = css`
    :host {display:block;container-type:inline-size}
    .toolbar,header {display:flex;align-items:baseline;justify-content:space-between;gap:6px 12px;flex-wrap:wrap}
    .toolbar {justify-content:flex-start;margin:0 0 16px}
    button {font:inherit;color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:6px;padding:8px 12px;cursor:pointer}
    button:hover {background:var(--secondary-background-color)}
    button:focus-visible {outline:2px solid var(--primary-color);outline-offset:3px}
    button:disabled {cursor:wait;opacity:.6}
    .groups {display:grid;grid-template-columns:minmax(0,1fr);gap:12px}
    section {min-width:0;border:1px solid var(--divider-color);border-radius:var(--ha-card-border-radius,12px);padding:14px;overflow-wrap:anywhere}
    h3,h4,p {margin:0}
    h3 {font-size:1rem}
    h4 {font-size:.9rem;font-weight:500}
    p,dt,.status {font-size:.875rem;line-height:1.5;color:var(--secondary-text-color)}
    p {margin-top:6px}
    .status {font-weight:500}
    .status[data-status="ready"] {color:var(--primary-text-color)}
    .status[data-status="needs_attention"] {color:var(--error-color,var(--primary-text-color))}
    ul {list-style:none;padding:0;margin:0}
    li {padding-top:10px;margin-top:10px;border-top:1px solid var(--divider-color)}
    dl {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 12px;margin:8px 0 0}
    dd {margin:2px 0 0;font-variant-numeric:tabular-nums}
    .settings {margin:14px 0 0}
    @container (min-width:690px) {.groups {grid-template-columns:repeat(2,minmax(0,1fr));align-items:start}}
  `;
}
