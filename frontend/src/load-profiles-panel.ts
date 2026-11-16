import {LitElement, css, html, nothing, type PropertyValues} from "lit";
import {customElement, property, state} from "lit/decorators.js";
import {live} from "lit/directives/live.js";
import type {HomeAssistant} from "./types/hass";

export interface SavedLoad {
  id: string;
  name: string;
  power_w: number;
  duration_minutes: number;
}

interface LoadList {
  profiles: SavedLoad[];
  revision: number;
  storage_error: boolean;
}

interface LoadEditor {
  id?: string;
  name: string;
  power: string;
  minutes: string;
  revision: number;
}

@customElement("solar-sanity-load-profiles")
export class SolarSanityLoadProfiles extends LitElement {
  @property({attribute:false}) public hass?: HomeAssistant;
  @property() public deviceId = "";
  @property({type:Boolean}) public active = false;
  @property() public power = "";
  @property() public minutes = "";
  @property({attribute:false}) public selected?: SavedLoad;
  @state() private _list?: LoadList;
  @state() private _reading = false;
  @state() private _writing = false;
  @state() private _readError = false;
  @state() private _writeError = "";
  @state() private _message = "";
  @state() private _editor?: LoadEditor;
  @state() private _delete?: {load: SavedLoad; revision: number};
  private _request = 0;
  private _scope = 0;

  public override connectedCallback(): void {
    super.connectedCallback();
    if (this.hasUpdated) void this._read();
  }

  public override disconnectedCallback(): void {
    super.disconnectedCallback();
    this._reset();
  }

  protected override updated(changed: PropertyValues): void {
    if (changed.has("deviceId")) this._reset();
    const previous = changed.get("hass") as HomeAssistant | undefined;
    if (changed.has("deviceId") || changed.has("active") ||
        (changed.has("hass") && previous?.config.state !== this.hass?.config.state)) {
      void this._read();
    }
  }

  private _reset(): void {
    this._scope++;
    this._list = undefined;
    this._reading = this._writing = this._readError = false;
    this._editor = this._delete = undefined;
    this._writeError = this._message = "";
  }

  private _call(service: string, data: Record<string, unknown> = {}) {
    return this.hass!.callWS<{response: LoadList}>({
      type: "call_service", domain: "solar_sanity", service, return_response: true,
      service_data: {device_id: this.deviceId, ...data},
    });
  }

  private _accept(response: LoadList): void {
    if (!Array.isArray(response?.profiles) || !Number.isSafeInteger(response.revision)) {
      throw new Error("The saved load list is unavailable.");
    }
    this._list = response;
    const selected = this.selected;
    if (selected && !response.storage_error && !response.profiles.some(load =>
      load.id === selected.id && load.name === selected.name && load.power_w === selected.power_w &&
      load.duration_minutes === selected.duration_minutes)) {
      this._select();
      this._message = "The saved load changed. Your current values are still in the form.";
    }
  }

  private async _read(): Promise<void> {
    if (!this.active || !this.isConnected || !this.deviceId || this._writing ||
        this.hass?.config.state !== "RUNNING") return;
    const scope = this._scope;
    const request = ++this._request;
    const device = this.deviceId;
    const current = () => scope === this._scope && request === this._request && device === this.deviceId;
    this._reading = true;
    this._readError = false;
    try {
      const {response} = await this._call("list_load_profiles");
      if (current()) this._accept(response);
    } catch {
      if (current()) this._readError = true;
    } finally {
      if (current()) this._reading = false;
    }
  }

  private _select(load?: SavedLoad): void {
    this.dispatchEvent(new CustomEvent<SavedLoad | undefined>("saved-load-change", {detail: load}));
  }

  private _open(load?: SavedLoad): void {
    if (!this._list) return;
    this._delete = undefined;
    this._writeError = this._message = "";
    this._editor = {
      id: load?.id, name: load?.name ?? "", power: load ? String(load.power_w) : this.power,
      minutes: load ? String(load.duration_minutes) : this.minutes, revision: this._list.revision,
    };
    void this.updateComplete.then(() => this.renderRoot.querySelector<HTMLInputElement>("[name=load_name]")?.focus());
  }

  private _close(): void {
    const focus = this._editor?.id || this._delete ? "summary" : "#save-as-load";
    this._editor = this._delete = undefined;
    this._writeError = "";
    void this.updateComplete.then(() => this.renderRoot.querySelector<HTMLElement>(focus)?.focus());
  }

  private async _write(service: string, data: Record<string, unknown>, message: string): Promise<void> {
    if (this._writing || this._reading || this._readError || this._list?.storage_error || !this.hass?.user?.is_admin) return;
    const scope = this._scope;
    const device = this.deviceId;
    const current = () => scope === this._scope && device === this.deviceId;
    this._writing = true;
    this._writeError = this._message = "";
    let refresh = false;
    try {
      const {response} = await this._call(service, data);
      if (!current()) return;
      this._message = message;
      this._accept(response);
      this._close();
    } catch (error) {
      if (!current()) return;
      this._message = "";
      const reason = (error as {message?: unknown})?.message;
      this._writeError = typeof reason === "string" ? reason : "Could not update saved loads.";
      refresh = true;
    } finally {
      if (current()) this._writing = false;
    }
    if (refresh) {
      await this._read();
      if (current() && this._list && !this._readError && !this._list.storage_error &&
          this._list.revision !== data.revision) {
        this._writeError = "Saved loads have changed. Cancel and reopen this edit to use the latest list.";
      }
    }
  }

  private _save(event: Event): void {
    event.preventDefault();
    if (!this._editor) return;
    const form = new FormData(event.currentTarget as HTMLFormElement);
    const name = String(form.get("load_name") ?? "").trim();
    if (!name || Array.from(name).length > 60) {
      this._writeError = "Enter a name of 1 to 60 characters.";
      return;
    }
    const {id, revision} = this._editor;
    void this._write("save_load_profile", {
      ...(id ? {profile_id: id} : {}), revision, name, power_w: Number(form.get("load_power")),
      duration_minutes: Number(form.get("load_minutes")),
    }, "Load saved.");
  }

  protected override render() {
    const loads = this._list?.profiles ?? [];
    const unavailable = this._readError || this._list?.storage_error;
    const busy = this._reading || this._writing;
    const canManage = this.hass?.user?.is_admin && this._list && !unavailable;
    const editor = this._editor;
    const deletion = this._delete;
    const select = (event: Event) => this._select(loads.find(load => load.id === (event.target as HTMLSelectElement).value));
    return html`
      <div class="picker">
        <label>Saved load<select @change=${select} ?disabled=${busy || unavailable || !loads.length}>
          <option value="" .selected=${live(!loads.some(load => load.id === this.selected?.id))}>Manual</option>
          ${loads.map(load => html`<option value=${load.id} .selected=${live(load.id === this.selected?.id)}>${load.name}</option>`)}
        </select></label>
        ${canManage && loads.length < 20 ? html`<button id="save-as-load" type="button" ?disabled=${busy} @click=${() => this._open()}>Save as load</button>` : nothing}
      </div>
      ${this._reading ? html`<p role="status">Loading saved loads…</p>` : nothing}
      ${unavailable ? html`<p role="status">Saved loads are unavailable. You can enter the load manually.</p>
        ${this._readError ? html`<button type="button" @click=${() => void this._read()}>Retry saved loads</button>` : nothing}` : nothing}
      ${canManage ? html`
        <details>
          <summary>Manage saved loads</summary>
          ${loads.length === 0 ? html`<p>No loads saved yet.</p>` : html`<ul>${loads.map(load => html`
            <li><div><strong dir="auto">${load.name}</strong><small>${load.power_w} W · ${load.duration_minutes} min</small></div>
              <div class="actions"><button type="button" aria-label=${`Edit ${load.name}`} ?disabled=${busy} @click=${() => this._open(load)}>Edit</button>
                <button type="button" aria-label=${`Delete ${load.name}`} ?disabled=${busy} @click=${() => {
                  this._editor = undefined;
                  this._writeError = this._message = "";
                  this._delete = {load, revision: this._list!.revision};
                  void this.updateComplete.then(() => this.renderRoot.querySelector<HTMLElement>("#cancel-delete")?.focus());
                }}>Delete</button></div>
            </li>`)}</ul>`}
          ${loads.length === 20 ? html`<p>This installation has reached its limit of 20 saved loads.</p>` : nothing}
        </details>
        ${editor ? html`<form @submit=${(event: Event) => this._save(event)}>
          <label>Load name<input name="load_name" required .value=${editor.name} ?disabled=${busy}></label>
          <div class="fields">
            <label>Power (W)<input name="load_power" type="number" min="0" step="any" required .value=${editor.power} ?disabled=${busy}></label>
            <label>Duration (minutes)<input name="load_minutes" type="number" min="1" max="1440" step="1" required .value=${editor.minutes} ?disabled=${busy}></label>
          </div>
          <div class="actions"><button type="submit" ?disabled=${busy}>${editor.id ? "Save changes" : "Save load"}</button>
            <button type="button" ?disabled=${busy} @click=${() => this._close()}>Cancel</button></div>
        </form>` : nothing}
        ${deletion ? html`<div class="confirm"><p>Delete <strong dir="auto">${deletion.load.name}</strong>?</p><div class="actions">
          <button type="button" ?disabled=${busy} @click=${() => void this._write("delete_load_profile", {
            profile_id: deletion.load.id, revision: deletion.revision,
          }, "Load deleted.")}>Delete load</button>
          <button type="button" id="cancel-delete" ?disabled=${busy} @click=${() => this._close()}>Cancel</button>
        </div></div>` : nothing}` : nothing}
      ${this._writing ? html`<p role="status">Updating saved loads…</p>` : nothing}
      ${this._writeError ? html`<p role="alert">${this._writeError}</p>` : nothing}
      ${this._message ? html`<p role="status">${this._message}</p>` : nothing}
    `;
  }

  public static styles = css`
    :host{display:block;container-type:inline-size;margin-block-end:16px}
    .picker,.actions{display:flex;align-items:end;flex-wrap:wrap;gap:8px}
    .picker label{flex:1 1 160px;max-inline-size:100%;min-inline-size:0}
    label{display:grid;gap:6px;font-size:.875rem}
    input,select,button{box-sizing:border-box;font:inherit;font-size:1rem;color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:6px;padding:10px;min-block-size:44px}
    input,select{width:100%;min-width:0}
    button,summary{cursor:pointer}button:disabled{opacity:.65;cursor:default}button:not(:disabled):hover{background:var(--secondary-background-color)}
    :is(input,select,button,summary):focus-visible{outline:2px solid var(--primary-color);outline-offset:3px}
    details,form,.confirm{margin-block-start:12px;border-block-start:1px solid var(--divider-color);padding-block-start:12px}
    summary{width:fit-content}form,.fields{display:grid;gap:10px}.fields{grid-template-columns:repeat(2,minmax(0,1fr))}
    ul{list-style:none;padding:0;margin:0}li{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;border-block-end:1px solid var(--divider-color);padding-block:8px}
    li div{min-inline-size:0}strong{overflow-wrap:anywhere}small{display:block;color:var(--secondary-text-color)}p{margin-block:8px;overflow-wrap:anywhere}
    @container(max-width:360px){.fields{grid-template-columns:1fr}}
  `;
}
