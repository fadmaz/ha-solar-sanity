/** Tomorrow's corrected solar forecast. This card has no currency figures. */
import { LitElement, css, html, nothing, svg, type SVGTemplateResult, type TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import { qualityPanel, qualityStyles } from "./quality";
import { areaPath, hourLabels, linePath, niceTicks, round, scaleLinear } from "./chart";
import {
  correctedForecasts, hourLabel, localDayBounds, peak, safeTimeZone, tomorrow, total,
  type CorrectedForecast,
} from "./forecast-data";
import type { GridOptions, HomeAssistant, LovelaceCard, LovelaceCardConfig, LovelaceCardEditor } from "./types/hass";

const WIDTH = 600;
const HEIGHT = 180;
const PAD_START = 34;
const PAD_END = 8;
const PAD_TOP = 10;
const PAD_BOTTOM = 22;

interface ForecastCardConfig extends LovelaceCardConfig {
  provider?: string;
}


interface Panel {
  kwh: number;
  line: string;
  area: string;
  isolated: (readonly [number, number])[];
  ticks: number[];
  tickY: (value: number) => number;
  labels: {x: number; text: string}[];
  times: string[];
}

/** The x coordinate is elapsed time, so gaps occupy their actual width. */
export function buildPanel(forecast: CorrectedForecast, day: Date, zone: string): Panel {
  const {hours} = forecast;
  const {start, end} = localDayBounds(day, zone);
  const x = scaleLinear([start.getTime(), end.getTime()], [PAD_START, WIDTH - PAD_END]);
  const ticks = niceTicks(peak(hours));
  const y = scaleLinear([0, ticks.at(-1) ?? 1], [HEIGHT - PAD_BOTTOM, PAD_TOP]);
  const segments: (readonly [number, number])[][] = [];
  let segment: (readonly [number, number])[] = [];
  for (const [index, hour] of hours.entries()) {
    if (!index || hour.start.getTime() - hours[index - 1]!.start.getTime() !== 3_600_000) {
      segment = [];
      segments.push(segment);
    }
    segment.push([x(hour.start.getTime()), y(hour.kwh)]);
  }
  const wallTimes = hours.map(hour => hourLabel(hour.start, zone));
  const occurrences = new Map<string, number>();
  for (const time of wallTimes) occurrences.set(time, (occurrences.get(time) ?? 0) + 1);
  const times = wallTimes.map((time, index) => (occurrences.get(time) ?? 0) > 1
    ? hourLabel(hours[index]!.start, zone, true) : time);
  const stamps = hours.map(hour => hour.start);
  const marks = hourLabels(stamps, 6, when => hourLabel(when, zone));
  return {
    kwh: total(hours),
    line: segments.filter(points => points.length > 1).map(linePath).join(" "),
    area: segments.filter(points => points.length > 1)
      .map(points => areaPath(points, HEIGHT - PAD_BOTTOM)).join(" "),
    isolated: segments.filter(points => points.length === 1).map(points => points[0]!),
    ticks,
    tickY: value => round(y(value)),
    labels: marks.map((text, index) => ({
      text: text ? times[index]! : "",
      x: round(x(hours[index]!.start.getTime())),
    })),
    times,
  };
}

@customElement("solar-sanity-forecast-card")
export class SolarSanityForecastCard extends LitElement implements LovelaceCard {
  @property({attribute: false}) public hass?: HomeAssistant;
  @state() private _config?: ForecastCardConfig;
  private _clock?: ReturnType<typeof setTimeout>;
  private _wakeAt?: number;

  public setConfig(config: ForecastCardConfig): void {
    // Authoring errors only. Anything the data might do at runtime is a
    // sentence on the card, never a red box in the dashboard.
    if (config.provider !== undefined && typeof config.provider !== "string") {
      throw new Error("`provider` must be a statistic id");
    }
    this._config = config;
  }

  /** Statically imported; see the status card's note on why not lazily. */
  public static getConfigElement(): LovelaceCardEditor {
    return document.createElement("solar-sanity-forecast-card-editor");
  }

  public static getStubConfig(): ForecastCardConfig {
    return { type: "custom:solar-sanity-forecast-card" };
  }

  public getCardSize(): number {
    return 7;
  }

  /**
   * As tall as its content, for the same reason as the battery card's: one
   * panel per provider and a sentence that wraps on a narrow column cannot
   * be fitted to a fixed number of rows.
   */
  public getGridOptions(): GridOptions {
    return { rows: "auto", columns: 12, min_columns: 6 };
  }


  public override connectedCallback(): void {
    super.connectedCallback();
    this.requestUpdate();
  }

  public override disconnectedCallback(): void {
    super.disconnectedCallback();
    clearTimeout(this._clock);
    this._wakeAt = undefined;
  }

  private _forecast(now: Date, zone: string) {
    return correctedForecasts(this.hass!, tomorrow(now, zone), zone, now.getTime(), this._config?.provider);
  }

  protected override updated(): void {
    if (!this.isConnected || this.hass?.config.state !== "RUNNING") return;
    const now = new Date();
    const zone = safeTimeZone(this.hass.config.time_zone);
    const {forecasts} = this._forecast(now, zone);
    const wakeAt = Math.min(localDayBounds(now, zone).end.getTime(),
      ...forecasts.map(forecast => forecast.validUntil + 1));
    if (wakeAt === this._wakeAt) return;
    clearTimeout(this._clock);
    this._wakeAt = wakeAt;
    this._clock = setTimeout(() => {
      this._wakeAt = undefined;
      this.requestUpdate();
    }, Math.max(0, wakeAt - Date.now()));
  }

  protected render(): TemplateResult {
    if (!this.hass) return this._note("Connecting", "Waiting for Home Assistant.");
    if (this.hass.config.state !== "RUNNING") {
      return this._note("Home Assistant is still starting", "This will fill in by itself.");
    }
    const now = new Date();
    const zone = safeTimeZone(this.hass.config.time_zone);
    const day = tomorrow(now, zone);
    const {forecasts, reasons} = this._forecast(now, zone);
    if (!forecasts.length) {
      return this._note("Waiting for a corrected forecast",
        reasons.join(" ") || "Connect a forecast provider and wait for Solar Sanity to learn your roof.");
    }
    const date = day.toLocaleDateString("en", {weekday: "long", day: "numeric", month: "long", timeZone: zone});
    return html`
      <ha-card><div class="root" lang="en" dir="ltr">
        <header><h2>Tomorrow</h2><p class="when">${date}</p></header>
        ${forecasts.map(forecast => this._panel(forecast, day, zone))}
        ${reasons.map(reason => html`<p class="why">${reason}</p>`)}
        ${qualityPanel(this.hass, undefined, forecasts.map(forecast => forecast.latestId))}
      </div></ha-card>`;
  }

  private _panel(forecast: CorrectedForecast, day: Date, zone: string): TemplateResult {
    const panel = buildPanel(forecast, day, zone);
    const label = `${forecast.label}: ${panel.kwh.toFixed(1)} kilowatt hours expected from your roof for tomorrow`;
    return html`
      <section class="panel">
        <div class="heading">
          <span class="provider">${forecast.label}</span>
          <span class="total"><bdi>${panel.kwh.toFixed(1)} kWh</bdi></span>
        </div>
        ${this._roof(forecast)}
        <svg class="chart" viewBox="0 0 ${WIDTH} ${HEIGHT}"
          preserveAspectRatio="none" role="img" aria-label=${label}>
          ${panel.ticks.map(tick => svgLine(PAD_START, panel.tickY(tick), WIDTH - PAD_END, panel.tickY(tick)))}
          <path class="area" d=${panel.area} />
          <path class="line" d=${panel.line} />
          ${panel.isolated.map(([x, y]) => svg`<circle class="point" cx=${x} cy=${y} r="2" />`)}
          ${panel.ticks.map(tick => svg`<text class="tick" x="0" y=${panel.tickY(tick) + 4}>${tick}</text>`)}
          ${panel.labels.map(mark => mark.text
            ? svg`<text class="tick" x=${mark.x} y=${HEIGHT - 6}>${mark.text}</text>` : nothing)}
        </svg>
        <div class="sr-only"><table>
          <caption>${label}</caption>
          <tbody>${forecast.hours.map((hour, index) => html`<tr>
            <th scope="row">${panel.times[index]}</th><td>${hour.kwh.toFixed(2)} kWh</td>
          </tr>`)}</tbody>
        </table></div>
      </section>`;
  }

  private _roof(roof: CorrectedForecast): TemplateResult {
    const share = Math.round(roof.factor * 100);
    const over = roof.days ? ` over the last ${roof.days} days` : "";
    return html`<p class="why">
      ${roof.byKindOfDay
        ? html`Corrected for your roof: on days forecast like this one, it has delivered about <bdi>${share}%</bdi> of the forecast${over}.`
        : html`Corrected for your roof: it has delivered about <bdi>${share}%</bdi> of this forecast${over}.`}
    </p>`;
  }

  private _note(headline: string, body: string): TemplateResult {
    return html`
      <ha-card>
        <div class="root" lang="en" dir="ltr">
          <h2>${headline}</h2>
          <p class="body">${body}</p>
        </div>
      </ha-card>
    `;
  }

  static styles = css`
    ${qualityStyles}
    :host {
      display: block;
    }
    .root {
      position: relative;
      container-type: inline-size;
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-2, 8px);
      padding: var(--ha-space-4, 16px);
      box-sizing: border-box;
    }
    header {
      display: flex;
      align-items: baseline;
      gap: var(--ha-space-2, 8px);
      flex-wrap: wrap;
    }
    h2 {
      margin: 0;
      font-size: var(--ha-font-size-l, 1rem);
      font-weight: var(--ha-font-weight-medium, 500);
      color: var(--primary-text-color);
      text-align: start;
    }
    .when,
    .body {
      margin: 0;
      color: var(--secondary-text-color);
      font-size: var(--ha-font-size-m, 0.875rem);
      line-height: 1.45;
      text-align: start;
    }
    .heading {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: var(--ha-space-2, 8px);
    }
    .provider {
      color: var(--secondary-text-color);
      font-size: var(--ha-font-size-m, 0.875rem);
    }
    .total {
      color: var(--primary-text-color);
      font-size: var(--ha-font-size-l, 1rem);
      font-weight: var(--ha-font-weight-medium, 500);
      font-variant-numeric: tabular-nums;
    }
    .why {
      margin: 0;
      color: var(--secondary-text-color);
      font-size: var(--ha-font-size-s, 0.75rem);
      line-height: 1.45;
      text-align: start;
    }
    .chart {
      width: 100%;
      height: 140px;
      overflow: visible;
    }
    /* Home Assistant's own solar colour, so the card reads as part of the
       Energy dashboard rather than as a visitor. Both themes come free: these
       are custom properties, not values. */
    .line {
      fill: none;
      stroke: var(--energy-solar-color, #ff9800);
      stroke-width: 2;
      stroke-linejoin: round;
      vector-effect: non-scaling-stroke;
    }
    .point {fill: var(--primary-color);}

    .area {
      fill: var(--energy-solar-color, #ff9800);
      opacity: 0.16;
      stroke: none;
    }
    line {
      stroke: var(--divider-color);
      stroke-width: 1;
      vector-effect: non-scaling-stroke;
      opacity: 0.5;
    }
    .tick {
      fill: var(--secondary-text-color);
      font-size: 11px;
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
  `;
}

/**
 * A gridline. Extracted only so the template above stays readable.
 *
 * `svg`, not `html`: a nested template is parsed on its own, outside the
 * <svg> it lands in, and with `html` a <line> or <text> becomes an HTML element
 * of that name, which is in the DOM and drawn by no browser. Until 0.28.2 the
 * gridlines and both axes of this card were invisible for exactly that reason.
 */
function svgLine(x1: number, y1: number, x2: number, y2: number): SVGTemplateResult {
  return svg`<line x1=${x1} y1=${y1} x2=${x2} y2=${y2} />`;
}

declare global {
  interface HTMLElementTagNameMap {
    "solar-sanity-forecast-card": SolarSanityForecastCard;
  }
}
