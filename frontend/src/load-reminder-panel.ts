import {LitElement, css, html, nothing, type PropertyValues} from "lit";
import {customElement, property, state} from "lit/decorators.js";
import {safeTimeZone} from "./forecast-data";
import type {LoadPlanRequest} from "./simulation";
import type {HomeAssistant} from "./types/hass";

const labels = {
  scheduled: "Reminder scheduled",
  waiting: "Waiting to find a suitable time",
  dispatching: "Sending reminder",
  sent: "Reminder sent",
  cancelled: "Reminder cancelled",
  expired: "Expired",
  delivery_unconfirmed: "Delivery unconfirmed",
  failed: "Reminder could not be sent",
};

const reasons: Record<string, string> = {
  forecast_unavailable: "Waiting for a current corrected solar forecast.",
  outlook_unavailable: "Waiting for a valid battery outlook.",
  window_too_short: "The load no longer fits before the deadline.",
  beyond_horizon: "The deadline is beyond the current outlook.",
  baseline_reaches_reserve: "The battery is expected to reach reserve without the extra load.",
  all_times_reach_reserve: "The load would bring the battery to reserve at every tested start.",
  destination_unavailable: "Waiting for the notification destination to become available.",
  permission_revoked: "The person who scheduled this reminder no longer has administrator access.",
};

interface ReminderView {
  id: string;
  status: keyof typeof labels;
  reason_code: string | null;
  scheduled_at: string | null;
  latest_start: string;
  destination: string;
  power_w: number;
  duration_minutes: number;
}

interface ReminderResponse {
  reminder: ReminderView | null;
  storage_error: boolean;
}

@customElement("solar-sanity-load-reminder")
export class SolarSanityLoadReminder extends LitElement {
  @property({attribute: false}) public hass?: HomeAssistant;
  @property() public deviceId = "";
  @property() public projectionId = "";
  @property({type: Boolean}) public active = false;
  @property({attribute: false}) public request?: LoadPlanRequest;

  @state() private _result?: ReminderResponse;
  @state() private _destination = "";
  @state() private _saving = false;
  @state() private _error = "";
  @state() private _statusError = false;
  private _generation = 0;
  private _timer?: ReturnType<typeof setTimeout>;
  private readonly _visibility = () => this._resetPoll();

  public override connectedCallback(): void {
    super.connectedCallback();
    document.addEventListener("visibilitychange", this._visibility);
    if (this.hasUpdated) this._resetPoll();
  }

  public override disconnectedCallback(): void {
    super.disconnectedCallback();
    document.removeEventListener("visibilitychange", this._visibility);
    this._resetPoll();
  }

  protected updated(changed: PropertyValues): void {
    if (changed.has("deviceId")) {
      this._result = undefined;
      this._destination = "";
      this._error = "";
      this._statusError = false;
    }
    if (changed.has("active") || changed.has("deviceId")) this._resetPoll();
  }

  private _visible(): boolean {
    return Boolean(this.active && this.deviceId && this.hass && this.isConnected && !document.hidden);
  }

  private _resetPoll(): void {
    this._generation++;
    clearTimeout(this._timer);
    this._saving = false;
    if (this._visible()) void this._refresh(this._generation);
  }

  private async _call(service: string, data: Record<string, unknown>): Promise<ReminderResponse> {
    const {response} = await this.hass!.callWS<{response: ReminderResponse}>({
      type: "call_service",
      domain: "solar_sanity",
      service,
      service_data: {...data, device_id: this.deviceId},
      return_response: true,
    });
    if (!response || typeof response.storage_error !== "boolean") {
      throw new Error("Reminder status is unavailable.");
    }
    return response;
  }

  private async _refresh(generation: number): Promise<void> {
    try {
      const result = await this._call("get_load_reminder", {});
      if (generation === this._generation) {
        this._result = result;
        this._statusError = false;
      }
    } catch {
      if (generation === this._generation) this._statusError = true;
    } finally {
      if (generation === this._generation && this._visible()) {
        this._timer = setTimeout(() => void this._refresh(generation), 30_000);
      }
    }
  }

  private async _change(service: string, data: Record<string, unknown>): Promise<void> {
    if (!this._visible() || this._saving) return;
    const generation = ++this._generation;
    clearTimeout(this._timer);
    this._saving = true;
    this._error = "";
    try {
      const result = await this._call(service, data);
      if (generation === this._generation) this._result = result;
    } catch (error) {
      if (generation === this._generation) {
        const message = (error as {message?: unknown})?.message;
        this._error = typeof message === "string" ? message : "Could not change the reminder.";
      }
    } finally {
      if (generation === this._generation) {
        this._saving = false;
        if (this._visible()) void this._refresh(generation);
      }
    }
  }

  private _time(value: string): string {
    return new Intl.DateTimeFormat("en", {
      timeZone: safeTimeZone(this.hass?.config.time_zone),
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
      timeZoneName: "shortOffset",
    }).format(new Date(value));
  }

  protected render() {
    const result = this._result;
    const reminder = result?.reminder;
    const active = reminder?.status === "scheduled" || reminder?.status === "waiting";
    const canSchedule = this.request?.device_id === this.deviceId && result && !this._statusError && !result.storage_error && !active
      && reminder?.status !== "dispatching";
    const entities = Object.entries(this.hass?.states ?? {})
      .filter(([id, state]) => id.startsWith("notify.") && state.state !== "unavailable")
      .map(([id, state]) => ({value: id, label: state.attributes.friendly_name ?? id}));
    const legacy = Object.keys(this.hass?.services?.notify ?? {})
      .filter((id) => /^mobile_app_[a-z0-9_]+$/.test(id))
      .map((id) => ({value: id, label: `notify.${id}`}));
    const destinations = [...entities, ...legacy];
    const selected = destinations.some(({value}) => value === this._destination);
    if (!reminder && !this.request && !result?.storage_error && !this._statusError && !this._error) return nothing;

    return html`<section aria-label="Load reminder">
      <div role="status">
        ${result?.storage_error ? html`<p>The reminder store is unavailable.</p>` : nothing}
        ${this._statusError ? html`<p>Could not refresh the reminder status. Retrying…</p>` : nothing}
        ${!result && !this._statusError && this.request ? html`<p>Checking reminder status…</p>` : nothing}
        ${reminder ? html`
          <p><strong>${labels[reminder.status]}</strong> · ${reminder.power_w} W for ${reminder.duration_minutes} min</p>
          ${reminder.scheduled_at && active ? html`<p>Scheduled start ${this._time(reminder.scheduled_at)}</p>` : nothing}
          ${active ? html`<p>Latest start ${this._time(reminder.latest_start)}</p>` : nothing}
          ${reminder.reason_code ? html`<p>${reasons[reminder.reason_code]}</p>` : nothing}
        ` : nothing}
      </div>
      ${active && reminder ? html`<button ?disabled=${this._saving}
        @click=${() => void this._change("cancel_load_reminder", {reminder_id: reminder.id})}>Cancel reminder</button>` : nothing}
      ${canSchedule ? html`
        ${destinations.length ? html`<div class="controls">
          <label>Notification destination
            <select .value=${selected ? this._destination : ""} ?disabled=${this._saving}
              @change=${(event: Event) => { this._destination = (event.target as HTMLSelectElement).value; }}>
              <option value="">Choose a destination</option>
              ${destinations.map(({value, label}) => html`<option value=${value}>${label}</option>`)}
            </select>
          </label>
          <button ?disabled=${!selected || this._saving}
            @click=${() => void this._change("schedule_load_reminder", {
              ...this.request, projection_id: this.projectionId, destination: this._destination,
            })}>Remind me</button>
        </div>` : html`<p>Add a Home Assistant notification destination to set a reminder.</p>`}
      ` : nothing}
      ${this._error ? html`<p role="alert">${this._error}</p>` : nothing}
    </section>`;
  }

  public static styles = css`
    section { margin-block: 16px; padding-block-start: 12px; border-block-start: 1px solid var(--divider-color); }
    p { margin: 0 0 8px; line-height: 1.6; }
    .controls { display: flex; align-items: end; flex-wrap: wrap; gap: 8px; }
    label { display: grid; gap: 6px; font-size: .875rem; min-width: 0; flex: 1 1 15rem; }
    select, button {
      box-sizing: border-box; font: inherit; color: var(--primary-text-color);
      background: var(--card-background-color); border: 1px solid var(--divider-color);
      border-radius: 6px; padding: 10px; min-width: 0; min-height: 44px;
    }
    select { width: 100%; }
    button { cursor: pointer; }
    button:disabled { opacity: .6; cursor: default; }
    button:not(:disabled):hover { background: var(--secondary-background-color); }
    :is(select, button):focus-visible { outline: 2px solid var(--primary-color); outline-offset: 3px; }
  `;
}
