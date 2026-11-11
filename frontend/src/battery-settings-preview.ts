import { LitElement, css, html, nothing, type PropertyValues, type TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import { powerControlLabel, type BatteryControls } from "./battery-controls";
import { safeTimeZone } from "./forecast-data";
import type { HomeAssistant } from "./types/hass";

interface Projection {
  lowest_pct: number | null;
  reserve_at: string | null;
  full_at: string | null;
  takeover_at: string | null;
  controls: BatteryControls;
}

export interface PreviewResponse {
  basis: {projection_id: string; computed_at: string; time_zone: string};
  current: Projection;
  proposed: Projection;
}

function watts(value: string): number | null {
  const text = value.trim();
  if (!text) return null;
  return /^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(text) ? Number(text) : NaN;
}

@customElement("solar-sanity-battery-settings-preview")
export class SolarSanityBatterySettingsPreview extends LitElement {
  @property({attribute: false}) public hass?: HomeAssistant;
  @property() public deviceId = "";
  @property() public projectionId = "";
  @property({attribute: false}) public controls?: BatteryControls;
  @property({type: Boolean}) public active = false;
  @state() private _open = false;
  @state() private _started = false;
  @state() private _limit = "";
  @state() private _standby = "";
  @state() private _result?: PreviewResponse;
  @state() private _loading = false;
  @state() private _error = "";
  private _limitEdited = false;
  private _standbyEdited = false;
  private _generation = 0;
  private _timer?: ReturnType<typeof setTimeout>;
  private _running = false;
  private _queued = false;
  private readonly _visibility = () => this._schedule();

  public override connectedCallback(): void {
    super.connectedCallback();
    document.addEventListener("visibilitychange", this._visibility);
    this._schedule();
  }

  public override disconnectedCallback(): void {
    super.disconnectedCallback();
    document.removeEventListener("visibilitychange", this._visibility);
    this._schedule();
  }

  protected override updated(changed: PropertyValues<this>): void {
    if (changed.has("deviceId")) {
      this._limitEdited = false;
      this._standbyEdited = false;
    }
    if (changed.has("controls") || changed.has("deviceId")) {
      if (!this._limitEdited) this._limit = String(this.controls?.battery_charge_power_limit_w.manual_w ?? "");
      if (!this._standbyEdited) this._standby = String(this.controls?.inverter_standby_w.manual_w ?? "");
    }
    if (changed.has("projectionId") || changed.has("active") || changed.has("deviceId")
      || (changed.has("controls") && Boolean(changed.get("controls")) !== Boolean(this.controls))) {
      this._schedule();
    }
  }

  private _validation(): string | null {
    const limit = watts(this._limit), standby = watts(this._standby);
    if (limit !== null && (!Number.isFinite(limit) || limit <= 0)) {
      return "Enter a finite charge power limit above zero.";
    }
    if (standby !== null && (!Number.isFinite(standby) || standby < 0)) {
      return "Enter a finite standby draw of zero or more.";
    }
    return null;
  }

  private _canPreview(): boolean {
    return Boolean(this.active && this.isConnected && !document.hidden && this._open
      && this._started && this.hass && this.deviceId && this.projectionId && this.controls
      && !this._validation());
  }

  private _schedule(): void {
    this._generation++;
    clearTimeout(this._timer);
    this._queued = false;
    this._result = undefined;
    this._error = "";
    this._loading = this._canPreview();
    if (this._loading) this._timer = setTimeout(() => void this._preview(), 500);
  }

  private async _preview(): Promise<void> {
    if (!this._canPreview()) return;
    if (this._running) {
      this._queued = true;
      return;
    }
    const generation = this._generation;
    const projection = this.projectionId;
    this._running = true;
    try {
      const {response} = await this.hass!.callWS<{response: PreviewResponse}>({
        type: "call_service",
        domain: "solar_sanity",
        service: "preview_battery_settings",
        service_data: {
          device_id: this.deviceId,
          projection_id: projection,
          battery_charge_power_limit_w: watts(this._limit),
          inverter_standby_w: watts(this._standby),
        },
        return_response: true,
      });
      if (generation !== this._generation || !this._canPreview() || projection !== this.projectionId) return;
      if (response?.basis?.projection_id !== projection || !response.current?.controls || !response.proposed?.controls) {
        throw new Error("The preview response is unavailable. Try again.");
      }
      this._result = response;
    } catch (error) {
      if (generation === this._generation && this._canPreview()) {
        const message = (error as {message?: unknown})?.message;
        this._error = typeof message === "string" ? message : "The preview could not be calculated.";
      }
    } finally {
      this._running = false;
      if (generation === this._generation) this._loading = false;
      if (this._queued) this._schedule();
    }
  }

  private _time(value: string | null, zone: string): string {
    if (!value) return "—";
    return new Intl.DateTimeFormat("en", {
      timeZone: safeTimeZone(zone), month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).format(new Date(value));
  }

  private _comparison(result: PreviewResponse): TemplateResult {
    const zone = result.basis.time_zone;
    const time = (value: string | null) => this._time(value, zone);
    const minimum = (value: number | null) => value === null ? "—" : `${value.toFixed(1)}%`;
    const {current, proposed} = result;
    const rows = [
      ["Charge power limit", powerControlLabel(current.controls.battery_charge_power_limit_w),
        powerControlLabel(proposed.controls.battery_charge_power_limit_w)],
      ["Inverter standby", powerControlLabel(current.controls.inverter_standby_w),
        powerControlLabel(proposed.controls.inverter_standby_w)],
      ["Night minimum", minimum(current.lowest_pct), minimum(proposed.lowest_pct)],
      ["Reserve at", time(current.reserve_at), time(proposed.reserve_at)],
      ["Full at", time(current.full_at), time(proposed.full_at)],
      ["Solar covers load at", time(current.takeover_at), time(proposed.takeover_at)],
    ];
    return html`<p class="muted">Based on ${time(result.basis.computed_at)} · Calculated times</p>
      <table aria-label="Battery settings comparison"><thead><tr>
        <th scope="col">Result</th><th scope="col">Current</th><th scope="col">Proposed</th>
      </tr></thead><tbody>${rows.map(([label, before, after]) => html`
        <tr><th scope="row">${label}</th><td>${before}</td><td>${after}</td></tr>
      `)}</tbody></table>`;
  }

  protected override render(): TemplateResult {
    const invalid = this._validation();
    const available = Boolean(this.projectionId && this.controls);
    return html`<details @toggle=${(event: Event) => {
      this._open = (event.currentTarget as HTMLDetailsElement).open;
      this._schedule();
    }}>
      <summary>Preview battery settings</summary>
      <p>Leave a field blank for Auto. Save settings in Solar Sanity’s <strong>Configure</strong> menu.</p>
      <div class="fields">
        <label>Battery charge power limit (W)
          <input inputmode="decimal" placeholder="Auto" .value=${this._limit} @input=${(event: Event) => {
            this._limitEdited = true;
            this._limit = (event.target as HTMLInputElement).value;
            this._schedule();
          }}>
        </label>
        <label>Inverter standby draw (W)
          <input inputmode="decimal" placeholder="Auto" .value=${this._standby} @input=${(event: Event) => {
            this._standbyEdited = true;
            this._standby = (event.target as HTMLInputElement).value;
            this._schedule();
          }}>
        </label>
      </div>
      <button ?disabled=${!available || Boolean(invalid) || this._loading}
        @click=${() => {this._started = true; this._schedule();}}>Preview</button>
      ${this._loading ? html`<span class="muted" role="status">Calculating preview…</span>` : nothing}
      ${invalid || this._error ? html`<p role="alert">${invalid ?? this._error}</p>` : nothing}
      ${!available ? html`<p class="muted">Waiting for a valid current projection.</p>` : nothing}
      ${this._result ? this._comparison(this._result) : nothing}
    </details>`;
  }

  public static styles = css`
    :host{display:block;margin-top:20px;border-top:1px solid var(--divider-color);padding-top:16px}
    summary{cursor:pointer;font-weight:500}
    p{line-height:1.5}
    .muted{color:var(--secondary-text-color);font-size:.875rem}
    [role=status]{margin-inline-start:12px}
    .fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:12px 0}
    label{display:grid;gap:6px;font-size:.875rem}
    input,button{box-sizing:border-box;min-width:0;font:inherit;color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:6px;padding:8px 12px}
    button{cursor:pointer}
    button:disabled{opacity:.6;cursor:default}
    summary:focus-visible,input:focus-visible,button:focus-visible{outline:2px solid var(--primary-color);outline-offset:3px}
    table{width:100%;table-layout:fixed;border-collapse:collapse;font-size:.875rem}
    td,th{text-align:left;padding:8px 4px;border-bottom:1px solid var(--divider-color);overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
    tbody th{font-weight:500}
    @media(max-width:420px){.fields{grid-template-columns:1fr}}
  `;
}
