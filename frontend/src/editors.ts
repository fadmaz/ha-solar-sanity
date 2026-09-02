/**
 * The two card editors.
 *
 * Both are thin on purpose. Home Assistant ships `<ha-form>` in its own bundle
 * and every integration's config flow renders through it, so a schema and a
 * label function buy the whole thing: themed inputs, keyboard behaviour,
 * translations, RTL, and whatever the frontend does next. Hand-rolling two
 * `<select>` elements would look right on the day it shipped and drift after.
 *
 * `<ha-form>` is used as a custom element rather than imported. It is defined
 * by the frontend before any dashboard renders, and importing from
 * `custom-card-helpers` to get a type for it would take a dependency this
 * project deliberately does not have.
 *
 * **Neither editor is required to configure its card.** The status card finds
 * its own entity and the forecast card its own provider, and a house with one
 * installation and one provider never needs either. That is the reason these
 * are small: an editor whose job is to be unnecessary should not be the largest
 * file in the bundle.
 */

import { LitElement, css, html, nothing, type TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import { type ArchiveMeta, dayAheadArchives, providerLabel } from "./forecast-data";
import { isStatusEntity } from "./status-card";
import type { HomeAssistant, LovelaceCardConfig } from "./types/hass";

/** One row of an `<ha-form>` schema. Only the shapes these editors use. */
interface FormRow {
  name: string;
  required?: boolean;
  selector: Record<string, unknown>;
}

interface HaFormElement extends HTMLElement {
  hass?: HomeAssistant;
  data?: Record<string, unknown>;
  schema?: readonly FormRow[];
  computeLabel?: (row: FormRow) => string;
  computeHelper?: (row: FormRow) => string | undefined;
}

/**
 * Emit the edited config.
 *
 * `bubbles` and `composed` are both load-bearing: the listener sits on the
 * dashboard's editor dialog, well above this element and across its shadow
 * boundary. Without `composed` the event stops at the shadow root and the
 * editor silently saves nothing — which looks exactly like a card that ignores
 * you.
 */
function emit(target: HTMLElement, config: LovelaceCardConfig): void {
  target.dispatchEvent(
    new CustomEvent("config-changed", {
      detail: { config },
      bubbles: true,
      composed: true,
    }),
  );
}

/**
 * Drop keys the form cleared.
 *
 * `<ha-form>` reports an emptied optional field as `undefined`, and writing
 * `{entity: undefined}` into a dashboard serialises as `entity: null` in YAML —
 * which is not the same as absent. Absent means "find your own"; null means the
 * card is asked to look up an entity called nothing.
 */
function pruned(data: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(data).filter(([, value]) => value !== undefined && value !== ""),
  );
}

const LABELS: (row: FormRow) => string = (row) =>
  ({
    entity: "Installation",
    provider: "Forecast provider",
  })[row.name] ?? row.name;

const HELPERS: (row: FormRow) => string | undefined = (row) =>
  ({
    entity: "Leave empty to use the only one you have.",
    provider: "Leave empty to show every provider you have configured.",
  })[row.name];

const SHARED = css`
  .found {
    padding: 12px 0 0;
    color: var(--secondary-text-color);
    font-size: 0.875rem;
    line-height: 1.4;
  }
  code {
    font-family: var(--code-font-family, monospace);
    word-break: break-all;
  }
`;

/**
 * The status card's editor.
 *
 * Its real job is the sentence underneath the field, not the field. The card
 * picks its own entity when only one installation exists, and somebody opening
 * this editor is usually asking *which one did it pick* — a question that had
 * no answer anywhere in the interface. Leaving the field blank is a supported
 * choice, and the editor says so rather than treating blank as unfinished.
 */
@customElement("solar-sanity-card-editor")
export class SolarSanityCardEditor extends LitElement {
  @property({ attribute: false }) public hass?: HomeAssistant;
  @state() private _config: LovelaceCardConfig = { type: "custom:solar-sanity-card" };

  public setConfig(config: LovelaceCardConfig): void {
    this._config = config;
  }

  /** Every status entity in the house, identified by what it publishes. */
  private get _candidates(): string[] {
    if (!this.hass) return [];
    const states = this.hass.states;
    return Object.keys(states)
      .filter((id) => id.startsWith("sensor.") && isStatusEntity(states[id]))
      .sort();
  }

  private get _schema(): FormRow[] {
    return [
      {
        name: "entity",
        // `include_entities` rather than a domain filter. Every integration in
        // the house publishes sensors, and a picker offering four hundred of
        // them so you can choose one of two is a worse answer than no picker.
        selector: { entity: { include_entities: this._candidates } },
      },
    ];
  }

  private _changed(event: CustomEvent): void {
    event.stopPropagation();
    const data = (event.detail as { value: Record<string, unknown> }).value;
    emit(this, { ...pruned(data), type: this._config.type } as LovelaceCardConfig);
  }

  protected render(): TemplateResult {
    return html`
      <ha-form
        .hass=${this.hass}
        .data=${this._config}
        .schema=${this._schema}
        .computeLabel=${LABELS}
        .computeHelper=${HELPERS}
        @value-changed=${this._changed}
      ></ha-form>
      ${this._note()}
    `;
  }

  private _note(): TemplateResult | typeof nothing {
    if (this._config.entity) return nothing;
    const candidates = this._candidates;

    if (candidates.length === 0) {
      return html`<div class="found">
        No Solar Sanity installation is reporting yet. The card will fill in by itself
        once one does.
      </div>`;
    }
    if (candidates.length === 1) {
      return html`<div class="found">
        Showing <code>${candidates[0]}</code>, found automatically. Leave the field empty
        unless you add a second installation.
      </div>`;
    }
    // The card refuses to guess between several and says so on its face. The
    // editor is where that gets fixed, so it should not be a surprise here.
    return html`<div class="found">
      ${candidates.length} installations are reporting. Choose one — the card will not
      pick for you, because a verdict about the wrong house is worse than no verdict.
    </div>`;
  }

  public static styles = SHARED;
}

/**
 * The forecast card's editor.
 *
 * The provider list comes from the recorder rather than from a hardcoded set of
 * integration names, so a provider this project has never heard of appears here
 * the day it writes its first archive — and one that has stopped writing stays
 * listed, which is the honest state of affairs rather than a field that
 * silently empties.
 */
@customElement("solar-sanity-forecast-card-editor")
export class SolarSanityForecastCardEditor extends LitElement {
  @property({ attribute: false }) public hass?: HomeAssistant;
  @state() private _config: LovelaceCardConfig = {
    type: "custom:solar-sanity-forecast-card",
  };
  @state() private _archives?: ArchiveMeta[];
  /** A load in progress, so a burst of `hass` updates does not start five. */
  private _inflight?: Promise<void>;

  public setConfig(config: LovelaceCardConfig): void {
    this._config = config;
  }

  protected updated(): void {
    if (this.hass && this._archives === undefined) void this._load();
  }

  private async _load(): Promise<void> {
    if (this._inflight) return this._inflight;
    const hass = this.hass;
    if (!hass) return;

    this._inflight = (async () => {
      try {
        const all = await hass.callWS<ArchiveMeta[]>({
          type: "recorder/list_statistic_ids",
          statistic_type: "sum",
        });
        this._archives = dayAheadArchives(all ?? []);
      } catch {
        // An empty list, not a failure state. The editor still works — the
        // field falls back to free text, which is what somebody typing a
        // statistic id by hand needs anyway.
        this._archives = [];
      } finally {
        this._inflight = undefined;
      }
    })();
    return this._inflight;
  }

  private get _schema(): FormRow[] {
    const archives = this._archives ?? [];
    if (archives.length === 0) return [{ name: "provider", selector: { text: {} } }];

    return [
      {
        name: "provider",
        selector: {
          select: {
            mode: "dropdown",
            // Not `custom_value`. The value is a statistic id, and a typo in
            // one renders an empty chart with no explanation — the picker
            // exists so that cannot happen.
            options: archives.map((meta) => ({
              value: meta.statistic_id,
              label: providerLabel(meta),
            })),
          },
        },
      },
    ];
  }

  private _changed(event: CustomEvent): void {
    event.stopPropagation();
    const data = (event.detail as { value: Record<string, unknown> }).value;
    emit(this, { ...pruned(data), type: this._config.type } as LovelaceCardConfig);
  }

  protected render(): TemplateResult {
    return html`
      <ha-form
        .hass=${this.hass}
        .data=${this._config}
        .schema=${this._schema}
        .computeLabel=${LABELS}
        .computeHelper=${HELPERS}
        @value-changed=${this._changed}
      ></ha-form>
      ${this._note()}
    `;
  }

  private _note(): TemplateResult | typeof nothing {
    if (this._archives === undefined || this._config.provider) return nothing;

    if (this._archives.length === 0) {
      return html`<div class="found">
        No day-ahead archive has been written yet. Solar Sanity starts one the first time
        it records a forecast, which is within a day of you configuring a provider.
      </div>`;
    }
    const only = this._archives.length === 1 ? this._archives[0] : undefined;
    if (only) {
      return html`<div class="found">
        Showing ${providerLabel(only)}, the only provider archiving so far.
      </div>`;
    }
    // Several is the case the card handles well — it draws them side by side —
    // so this is a statement rather than a prompt to choose.
    return html`<div class="found">
      Showing all ${this._archives.length} providers side by side. Choose one to narrow
      it.
    </div>`;
  }

  public static styles = SHARED;
}

declare global {
  interface HTMLElementTagNameMap {
    "ha-form": HaFormElement;
    "solar-sanity-card-editor": SolarSanityCardEditor;
    "solar-sanity-forecast-card-editor": SolarSanityForecastCardEditor;
  }
}
