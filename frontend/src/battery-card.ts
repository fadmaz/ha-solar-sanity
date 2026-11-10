/**
 * The battery card: where the charge is now, the one thing worth knowing about
 * the next day and night, three numbers, and the projected charge as a line.
 *
 * Everything comes from the five outlook sensors of one installation, found in
 * the entity registry (see `battery-model.ts`). The card computes nothing the
 * integration has not already published: a card that projected on its own
 * would sooner or later disagree with the sensors an automation is reading.
 *
 * The chart is drawn in two layers. The shapes are an SVG stretched to the
 * card's width, which is fine for lines and fills; the labels and the dots sit
 * on top as ordinary positioned elements, because text and circles inside a
 * stretched SVG come out squashed. The chart runs left to right in every
 * language, as time axes do.
 */

import { LitElement, css, html, nothing, svg, type TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import {
  CHART_HEIGHT,
  CHART_WIDTH,
  type ChartShape,
  type Clock,
  type Installation,
  type Outlook,
  chartShape,
  clockFor,
  figures,
  findInstallations,
  hasOutlook,
  headline,
  powerFlow,
  readOutlook,
} from "./battery-model";
import type {
  GridOptions,
  HomeAssistant,
  LovelaceCard,
  LovelaceCardConfig,
  LovelaceCardEditor,
} from "./types/hass";

export interface BatteryCardConfig extends LovelaceCardConfig {
  /** The installation to show, when there is more than one. */
  device_id?: string;
}

interface Note {
  headline: string;
  body: string;
}

@customElement("solar-sanity-battery-card")
export class SolarSanityBatteryCard extends LitElement implements LovelaceCard {
  @property({ attribute: false }) public hass?: HomeAssistant;
  @state() protected _config?: BatteryCardConfig;

  public setConfig(config: BatteryCardConfig): void {
    // Throw only for an authoring mistake. Anything else the card does not
    // recognise is ignored, so a configuration written for an older card
    // under the same tag keeps rendering rather than turning into a red box.
    if (config.device_id !== undefined && typeof config.device_id !== "string") {
      throw new Error("`device_id` must be a device id");
    }
    this._config = config;
  }

  public getCardSize(): number {
    return 8;
  }

  /**
   * As tall as its content, not a fixed number of rows. 0.34.0 added the live
   * flow and a second row of figures to a card fixed at five rows, and on the
   * reference installation's sections dashboard its chart then ran under the
   * card below it. The content is not a fixed height: a reason replaces the
   * figures, the flow line comes and goes, and figures wrap on a narrow column.
   */
  public getGridOptions(): GridOptions {
    return { rows: "auto", columns: 12, min_columns: 6 };
  }

  public static getConfigElement(): LovelaceCardEditor {
    return document.createElement("solar-sanity-battery-card-editor") as LovelaceCardEditor;
  }

  public static getStubConfig(): BatteryCardConfig {
    return { type: "custom:solar-sanity-battery-card" };
  }

  /** The installation to show, or the note explaining why there is none. */
  private _pick(hass: HomeAssistant): Installation | Note {
    const all = findInstallations(hass);
    const wanted = this._config?.device_id;
    if (wanted) {
      const found = all.find((installation) => installation.deviceId === wanted);
      if (!found) {
        return {
          headline: "Installation not found",
          body: "The device this card was set to show no longer exists. Choose another in the card's settings.",
        };
      }
      return hasOutlook(found) ? found : OUTLOOK_OFF;
    }
    if (all.length === 0) {
      return {
        headline: "Solar Sanity is not set up yet",
        body: "Add it under Settings, Devices & services.",
      };
    }
    const withOutlook = all.filter(hasOutlook);
    if (withOutlook.length === 0) return OUTLOOK_OFF;
    if (withOutlook.length > 1) {
      // Picking one silently would show one house's battery under a card the
      // reader may think is about the other.
      return {
        headline: "More than one installation",
        body: `This card found ${withOutlook.length}. Choose one in the card's settings.`,
      };
    }
    return withOutlook[0]!;
  }

  protected render(): TemplateResult {
    const hass = this.hass;
    if (!hass) return this._note({ headline: "Connecting", body: "Waiting for Home Assistant." });
    if (hass.config.state !== "RUNNING") {
      return this._note({
        headline: "Home Assistant is still starting",
        body: "This will fill in by itself.",
      });
    }

    const picked = this._pick(hass);
    if (!("deviceId" in picked)) return this._note(picked);

    const outlook = readOutlook(hass, picked);
    if (outlook.unavailable) {
      return this._note({
        headline: "The battery's charge level is not reporting",
        body: "The outlook comes back as soon as it does.",
      });
    }

    const clock = clockFor(hass.language, hass.config.time_zone);
    const now = new Date();
    return html`
      <ha-card>
        <div class="root">
          ${this._top(outlook, now, clock)} ${this._flow(hass, outlook)}
          ${outlook.reason
            ? html`<p class="reason">${outlook.reason}</p>`
            : html`${this._figures(outlook, now, clock)} ${this._chart(outlook, clock)}`}
        </div>
      </ha-card>
    `;
  }

  private _top(outlook: Outlook, now: Date, clock: Clock): TemplateResult {
    const lead = outlook.reason ? null : headline(outlook, now, clock);
    return html`
      <div class="top">
        <div class="soc" aria-label="Battery now" dir="ltr">
          ${outlook.socPct !== null
            ? html`<span class="number">${Math.round(outlook.socPct)}</span
                ><span class="unit">%</span>`
            : html`<span class="number">—</span>`}
        </div>
        <div class="lead" data-tone=${lead?.tone ?? "neutral"}>
          <h2 class="headline"><bdi>${lead?.text ?? "Battery outlook"}</bdi></h2>
          ${lead?.detail ? html`<p class="detail"><bdi>${lead.detail}</bdi></p>` : nothing}
        </div>
      </div>
    `;
  }

  /** The live flow, one line, or nothing when no power sensor is known. */
  private _flow(hass: HomeAssistant, outlook: Outlook): TemplateResult | typeof nothing {
    const items = powerFlow(hass, outlook.powerSources);
    if (items.length === 0) return nothing;
    return html`
      <p class="flow" aria-label="Power now">
        ${items.map(
          (item, index) =>
            html`${index > 0 ? html`<span class="sep" aria-hidden="true">·</span>` : nothing}<span
                class="flow-item"
                ><span class="flow-label">${item.label}</span>
                <bdi>${item.value}</bdi></span
              >`,
        )}
      </p>
    `;
  }

  private _figures(outlook: Outlook, now: Date, clock: Clock): TemplateResult {
    return html`
      <dl class="figures">
        ${figures(outlook, now, clock).map(
          (figure) => html`
            <div class="figure">
              <dt>${figure.label}</dt>
              <dd>
                <!-- bdi: "1.8 kWh" in a right-to-left page would otherwise come
                     out as "kWh 1.8". -->
                <span class="value"><bdi>${figure.value}</bdi></span>
                ${figure.sub ? html`<span class="sub"><bdi>${figure.sub}</bdi></span>` : nothing}
              </dd>
            </div>
          `,
        )}
      </dl>
    `;
  }

  private _chart(outlook: Outlook, clock: Clock): TemplateResult | typeof nothing {
    const shape = chartShape(outlook, clock);
    if (!shape) return nothing;
    const percent = (fraction: number) => `${Math.round(fraction * 10000) / 100}%`;
    const summary = this._summary(outlook, clock);
    return html`
      <div class="chart" dir="ltr">
        <svg
          viewBox="0 0 ${CHART_WIDTH} ${CHART_HEIGHT}"
          preserveAspectRatio="none"
          role="img"
          aria-label=${summary}
        >
          ${this._shapes(shape)}
        </svg>
        ${shape.reserve !== null
          ? html`<span class="ref" style="top: ${percent(shape.reserve)}"
              >Reserve ${Math.round(outlook.reservePct ?? 0)}%</span
            >`
          : nothing}
        ${shape.target !== null
          ? html`<span class="ref" style="top: ${percent(shape.target)}"
              >Target ${Math.round(outlook.targetPct ?? 0)}%</span
            >`
          : nothing}
        <span
          class="dot now"
          style="left: ${percent(shape.start.x)}; top: ${percent(shape.start.y)}"
        ></span>
        ${shape.marks.map(
          (mark) =>
            html`<span
              class="dot ${mark.kind}"
              style="left: ${percent(mark.x)}; top: ${percent(mark.y)}"
            ></span>`,
        )}
        <div class="hours" aria-hidden="true">
          ${shape.hours.map(
            (hour) => html`<span style="left: ${percent(hour.x)}">${hour.label}</span>`,
          )}
        </div>
      </div>
      <!-- The same numbers, for anyone who cannot see the line. -->
      <div class="sr-only">
      <table>
        <caption>
          ${summary}
        </caption>
        <tbody>
          ${outlook.trajectory.map(
            (point) =>
              html`<tr>
                <th scope="row">${clock.time(point.at)}</th>
                <td>${Math.round(point.soc)}%</td>
              </tr>`,
          )}
        </tbody>
      </table>
      </div>
    `;
  }

  /** The SVG layer. `svg`, not `html`: see `forecast-card.ts` for why it matters. */
  private _shapes(shape: ChartShape) {
    const x = (fraction: number) => fraction * CHART_WIDTH;
    const y = (fraction: number) => fraction * CHART_HEIGHT;
    return svg`
      ${shape.night
        ? svg`<rect class="night" x=${x(shape.night.from)} y="0"
            width=${Math.max(x(shape.night.to) - x(shape.night.from), 0)} height=${CHART_HEIGHT} />`
        : nothing}
      ${shape.reserve !== null
        ? svg`<line class="reserve" x1="0" x2=${CHART_WIDTH} y1=${y(shape.reserve)} y2=${y(shape.reserve)} />`
        : nothing}
      ${shape.target !== null
        ? svg`<line class="target" x1="0" x2=${CHART_WIDTH} y1=${y(shape.target)} y2=${y(shape.target)} />`
        : nothing}
      <path class="area" d=${shape.area} />
      <path class="line" d=${shape.line} />
    `;
  }

  private _summary(outlook: Outlook, clock: Clock): string {
    const first = outlook.trajectory[0];
    const last = outlook.trajectory[outlook.trajectory.length - 1];
    if (!first || !last) return "Projected battery charge";
    return `Projected battery charge from ${Math.round(first.soc)}% now to ${Math.round(
      last.soc,
    )}% at ${clock.time(last.at)}`;
  }

  private _note(note: Note): TemplateResult {
    return html`
      <ha-card>
        <div class="root">
          <h2 class="headline">${note.headline}</h2>
          <p class="detail">${note.body}</p>
        </div>
      </ha-card>
    `;
  }

  static styles = css`
    :host {
      display: block;
    }
    .root {
      position: relative;
      container-type: inline-size;
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-3, 12px);
      padding: var(--ha-space-4, 16px);
      box-sizing: border-box;
    }
    .top {
      display: flex;
      align-items: center;
      gap: var(--ha-space-4, 16px);
    }
    .soc {
      flex: none;
      color: var(--primary-text-color);
      line-height: 1;
    }
    .soc .number {
      font-size: 2.5rem;
      font-weight: var(--ha-font-weight-normal, 400);
    }
    .soc .unit {
      font-size: 1.1rem;
      color: var(--secondary-text-color);
      margin-inline-start: 2px;
    }
    .lead {
      min-width: 0;
      border-inline-start: 3px solid var(--divider-color);
      padding-inline-start: var(--ha-space-3, 12px);
    }
    /* Colour is the third signal, after the words and the position. */
    .lead[data-tone="good"] {
      border-inline-start-color: var(--success-color, #43a047);
    }
    .lead[data-tone="warn"] {
      border-inline-start-color: var(--warning-color, #ffa600);
    }
    .headline {
      margin: 0;
      font-size: var(--ha-font-size-l, 1rem);
      font-weight: var(--ha-font-weight-medium, 500);
      color: var(--primary-text-color);
      line-height: 1.3;
      text-align: start;
    }
    .detail,
    .reason {
      margin: 2px 0 0;
      color: var(--secondary-text-color);
      font-size: var(--ha-font-size-m, 0.875rem);
      line-height: 1.4;
      text-align: start;
    }
    .flow {
      margin: 0;
      display: flex;
      flex-wrap: wrap;
      gap: 2px var(--ha-space-2, 8px);
      color: var(--primary-text-color);
      font-size: 0.875rem;
      font-variant-numeric: tabular-nums;
    }
    .flow-label,
    .sep {
      color: var(--secondary-text-color);
    }
    .figures {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: var(--ha-space-2, 8px);
      margin: 0;
    }
    .figure {
      min-width: 0;
    }
    dt {
      color: var(--secondary-text-color);
      font-size: 0.75rem;
      line-height: 1.3;
    }
    dd {
      margin: 2px 0 0;
      display: flex;
      flex-direction: column;
    }
    .value {
      color: var(--primary-text-color);
      font-size: 1.05rem;
      font-weight: var(--ha-font-weight-medium, 500);
    }
    .sub {
      color: var(--secondary-text-color);
      font-size: 0.75rem;
    }
    .chart {
      position: relative;
      height: 120px;
      /* Room above for the target's label, which sits over the top line and
         would otherwise run into the last row of figures. */
      margin-block: 10px 16px;
    }
    svg {
      display: block;
      width: 100%;
      height: 100%;
      overflow: visible;
    }
    .line {
      fill: none;
      stroke: var(--primary-color, #03a9f4);
      stroke-width: 2;
      vector-effect: non-scaling-stroke;
    }
    .area {
      fill: var(--primary-color, #03a9f4);
      opacity: 0.12;
    }
    .night {
      fill: var(--primary-text-color);
      opacity: 0.06;
    }
    .reserve,
    .target {
      stroke-width: 1;
      stroke-dasharray: 4 4;
      vector-effect: non-scaling-stroke;
    }
    .reserve {
      stroke: var(--warning-color, #ffa600);
    }
    .target {
      stroke: var(--success-color, #43a047);
    }
    .ref {
      position: absolute;
      inset-inline-end: 0;
      transform: translateY(-115%);
      font-size: 0.7rem;
      color: var(--secondary-text-color);
      background: var(--ha-card-background, var(--card-background-color, #fff));
      padding: 0 2px;
      border-radius: 2px;
    }
    .dot {
      position: absolute;
      width: 8px;
      height: 8px;
      margin: -4px 0 0 -4px;
      border-radius: 50%;
      background: var(--primary-color, #03a9f4);
      box-shadow: 0 0 0 2px var(--ha-card-background, var(--card-background-color, #fff));
    }
    .dot.full {
      background: var(--success-color, #43a047);
    }
    .dot.reserve {
      background: var(--warning-color, #ffa600);
    }
    .hours {
      position: absolute;
      inset-inline: 0;
      top: 100%;
      height: 16px;
    }
    .hours span {
      position: absolute;
      top: 2px;
      transform: translateX(-50%);
      font-size: 0.7rem;
      color: var(--secondary-text-color);
    }
    /* On a wrapper, never on the table itself: a table is as wide as its
       content whatever its width says, and a wide invisible table pushes the
       page sideways on a phone. Positioned against the card, not the page. */
    .sr-only {
      position: absolute;
      inset-inline-start: 0;
      top: 0;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip-path: inset(50%);
      white-space: nowrap;
    }
    @container (max-width: 300px) {
      .figures {
        grid-template-columns: 1fr;
      }
    }
  `;
}

const OUTLOOK_OFF: Note = {
  headline: "The battery outlook is off",
  body: "Map the battery's charge level and enter its capacity in Solar Sanity, Configure.",
};

/**
 * The tag the status card had, kept so a dashboard built before 0.30.0 still
 * renders something rather than an error. It shows the battery card.
 *
 * A subclass, because one constructor cannot be registered under two names.
 * Not offered in the card picker: anything new should use the battery card's
 * own tag. Its old `entity:` setting named the status sensor, which no longer
 * exists, and `setConfig` ignores settings it does not recognise.
 */
export class SolarSanityLegacyCard extends SolarSanityBatteryCard {}

if (!customElements.get("solar-sanity-card")) {
  customElements.define("solar-sanity-card", SolarSanityLegacyCard);
}

declare global {
  interface HTMLElementTagNameMap {
    "solar-sanity-battery-card": SolarSanityBatteryCard;
    "solar-sanity-card": SolarSanityLegacyCard;
  }
}
