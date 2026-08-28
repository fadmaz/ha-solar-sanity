/**
 * Tomorrow's forecast, as it was issued rather than as it has been revised.
 *
 * The scope is deliberately narrow and will stay narrow until there is data to
 * widen it with. What this card shows is one curve: what a provider said a day
 * ahead about tomorrow, drawn from the archive the integration writes once per
 * hour at real lead time and never revises.
 *
 * What it does not show, and why:
 *
 *  - **Accuracy.** Nothing scores forecasts yet. A number here would have to be
 *    computed from the rolling archive, which holds the *latest* revision of
 *    every hour — so for any hour already past it holds a figure issued minutes
 *    after that hour ended. Scoring that flatters every provider equally and
 *    means nothing.
 *  - **Forecast against actual, as a shortfall.** On a system whose generation
 *    is measured before the inverter, or which exports through a meter nobody
 *    reads, the gap between a forecast and a sensor is not forecast error. Two
 *    lines and no third quantity.
 *  - **Anything in currency.** Never, anywhere in this product.
 */

import { LitElement, css, html, nothing, type TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import { areaPath, hourLabels, linePath, niceTicks, round, scaleLinear } from "./chart";
import {
  loadDayAhead,
  peak,
  tomorrow,
  total,
  type Hour,
  type ProviderDay,
} from "./forecast-data";
import type {
  GridOptions,
  HomeAssistant,
  LovelaceCard,
  LovelaceCardConfig,
} from "./types/hass";

/** Chart geometry, in user units. The viewBox scales it to whatever fits. */
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
  label: string;
  hours: Hour[];
  kwh: number;
  line: string;
  area: string;
  ticks: number[];
  tickY: (value: number) => number;
  labels: Array<{ x: number; text: string }>;
}

/** Everything the card needs to draw one provider's day. */
export function buildPanel(day: ProviderDay): Panel {
  const hours = day.hours;
  const top = niceTicks(peak(hours)).at(-1) ?? 1;

  const x = scaleLinear([0, Math.max(hours.length - 1, 1)], [PAD_START, WIDTH - PAD_END]);
  const y = scaleLinear([0, top], [HEIGHT - PAD_BOTTOM, PAD_TOP]);
  const points = hours.map((hour, index) => [x(index), y(hour.kwh)] as const);

  return {
    label: day.label,
    hours,
    kwh: total(hours),
    line: linePath(points),
    area: areaPath(points, HEIGHT - PAD_BOTTOM),
    ticks: niceTicks(peak(hours)),
    tickY: (value: number) => round(y(value)),
    labels: hourLabels(hours.map((hour) => hour.start)).map((text, index) => ({
      x: round(x(index)),
      text,
    })),
  };
}

@customElement("solar-sanity-forecast-card")
export class SolarSanityForecastCard extends LitElement implements LovelaceCard {
  @property({ attribute: false }) public hass?: HomeAssistant;
  @state() private _config?: ForecastCardConfig;
  @state() private _days?: ProviderDay[];
  @state() private _failed = false;
  @state() private _day = tomorrow(new Date());
  /** The lifecycle phase the last failure happened in. */
  private _failedDuring?: string;
  /** A load in progress, so a burst of hass updates does not start five. */
  private _inflight?: Promise<void>;

  public setConfig(config: ForecastCardConfig): void {
    // Authoring errors only. Anything the data might do at runtime is a
    // sentence on the card, never a red box in the dashboard.
    if (config.provider !== undefined && typeof config.provider !== "string") {
      throw new Error("`provider` must be a statistic id");
    }
    this._config = config;
  }

  public static getStubConfig(): ForecastCardConfig {
    return { type: "custom:solar-sanity-forecast-card" };
  }

  public getCardSize(): number {
    return 5;
  }

  public getGridOptions(): GridOptions {
    return { rows: 5, min_rows: 4, columns: 12, min_columns: 6 };
  }

  /**
   * Everything that must settle before this render, rather than after it.
   *
   * Setting reactive state from `updated` schedules a second pass over the
   * whole element; Lit says so out loud. These three are decisions about what
   * the current render should show, so they belong here, where they fold into
   * it.
   */
  protected willUpdate(changed: Map<string, unknown>): void {
    if (!changed.has("hass") || !this.hass) return;

    // A new lifecycle phase deserves another try. The usual way to see a
    // recorder refuse is a Home Assistant restart, and latching the failure
    // meant the card said "cannot read the record" for the rest of the day on
    // an installation whose recorder had been fine for hours.
    const phase = this.hass.config.state;
    if (phase !== this._failedDuring) this._failed = false;
    if (phase !== "RUNNING") return;

    // Midnight moves the question, and what is held answers yesterday's.
    const wanted = tomorrow(new Date());
    if (wanted.getTime() !== this._day.getTime()) {
      this._day = wanted;
      this._days = undefined;
      this._failed = false;
    }
  }

  public updated(changed: Map<string, unknown>): void {
    if (!changed.has("hass") || !this.hass) return;
    // Nothing to ask until the recorder can answer.
    if (this.hass.config.state !== "RUNNING") return;

    if (this._days === undefined && !this._failed && !this._inflight) {
      void this._load();
    }
  }

  private async _load(): Promise<void> {
    if (!this.hass) return;
    this._inflight = this._loadOnce(this._day).finally(() => {
      this._inflight = undefined;
    });
    return this._inflight;
  }

  private async _loadOnce(day: Date): Promise<void> {
    if (!this.hass) return;
    try {
      const days = await loadDayAhead(this.hass, day);
      this._days = this._config?.provider
        ? days.filter((d) => d.statisticId === this._config!.provider)
        : days;
    } catch {
      // The recorder can be disabled, and a card is not the place to explain a
      // stack trace. What the reader needs is one sentence and no red box.
      this._failed = true;
      this._failedDuring = this.hass.config.state;
    }
  }

  protected render(): TemplateResult {
    if (!this.hass) return this._note("Connecting", "Waiting for Home Assistant.");

    if (this.hass.config.state !== "RUNNING") {
      return this._note("Home Assistant is still starting", "This will fill in by itself.");
    }

    if (this._failed) {
      return this._note(
        "Cannot read the record",
        "Solar Sanity keeps its forecast history in Home Assistant's long-term statistics, and they are not available right now.",
      );
    }

    if (this._days === undefined) return this._note("Reading the record", "One moment.");

    if (this._days.length === 0) {
      return this._note(
        "No solar forecast to keep",
        "Add Forecast.Solar, Solcast or Open-Meteo, then pick it in Solar Sanity. It will record what they predict, which Home Assistant otherwise throws away within about ten days.",
      );
    }

    const panels = this._days.map(buildPanel);
    const withData = panels.filter((panel) => panel.hours.length > 1);

    if (withData.length === 0) {
      return this._note(
        "This record starts today",
        "Nothing was forecast for tomorrow far enough ahead to count yet. An hour is kept once it is at least twelve hours away, so tomorrow fills in through the evening.",
      );
    }

    return html`
      <ha-card>
        <div class="root">
          <header>
            <h2>${this._heading()}</h2>
            <p class="when">${this._dayLabel()}</p>
          </header>
          ${withData.map((panel) => this._panel(panel))}
          <p class="footnote">
            What was forecast a day ahead. Scoring it against what actually
            happens needs a completed day first.
          </p>
        </div>
      </ha-card>
    `;
  }

  /**
   * What the day is, relative to now rather than to when it was fetched.
   *
   * Derived at render rather than stored, so the heading cannot outlive its
   * own truth. At five past midnight a card loaded the previous evening still
   * holds the right *data*; calling it "Tomorrow" is the only part that would
   * have become false, and doing this here means no refresh has to win a race
   * for the card to stay honest.
   */
  private _heading(): string {
    const midnight = new Date();
    midnight.setHours(0, 0, 0, 0);
    const days = Math.round((this._day.getTime() - midnight.getTime()) / 86_400_000);
    if (days === 1) return "Tomorrow";
    if (days === 0) return "Today";
    return "Forecast";
  }

  private _dayLabel(): string {
    return this._day.toLocaleDateString(this.hass?.language ?? undefined, {
      weekday: "long",
      day: "numeric",
      month: "long",
    });
  }

  private _panel(panel: Panel): TemplateResult {
    const when = this._heading().toLowerCase();
    const label = `${panel.label}: ${panel.kwh.toFixed(1)} kilowatt hours forecast for ${when}`;
    return html`
      <section class="panel">
        <div class="heading">
          <span class="provider">${panel.label}</span>
          <span class="total">${panel.kwh.toFixed(1)} kWh</span>
        </div>
        <svg
          class="chart"
          viewBox="0 0 ${WIDTH} ${HEIGHT}"
          preserveAspectRatio="none"
          role="img"
          aria-label=${label}
        >
          ${panel.ticks.map(
            (tick) => svgLine(PAD_START, panel.tickY(tick), WIDTH - PAD_END, panel.tickY(tick)),
          )}
          <path class="area" d=${panel.area} />
          <path class="line" d=${panel.line} />
          ${panel.ticks.map(
            (tick) => html`<text class="tick" x="0" y=${panel.tickY(tick) + 4}>${tick}</text>`,
          )}
          ${panel.labels.map((mark) =>
            mark.text
              ? html`<text class="tick" x=${mark.x} y=${HEIGHT - 6}>${mark.text}</text>`
              : nothing,
          )}
        </svg>
        <!-- The same numbers, for anyone who cannot see the shape. -->
        <table class="sr-only">
          <caption>
            ${label}
          </caption>
          <tbody>
            ${panel.hours.map(
              (hour) => html`<tr>
                <th scope="row">${String(hour.start.getHours()).padStart(2, "0")}:00</th>
                <td>${hour.kwh.toFixed(2)} kWh</td>
              </tr>`,
            )}
          </tbody>
        </table>
      </section>
    `;
  }

  private _note(headline: string, body: string): TemplateResult {
    return html`
      <ha-card>
        <div class="root">
          <h2>${headline}</h2>
          <p class="body">${body}</p>
        </div>
      </ha-card>
    `;
  }

  static styles = css`
    :host {
      display: block;
    }
    .root {
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
    .body,
    .footnote {
      margin: 0;
      color: var(--secondary-text-color);
      font-size: var(--ha-font-size-m, 0.875rem);
      line-height: 1.45;
      text-align: start;
    }
    .footnote {
      font-size: var(--ha-font-size-s, 0.75rem);
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
    .sr-only {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip-path: inset(50%);
      white-space: nowrap;
    }
  `;
}

/** A gridline. Extracted only so the template above stays readable. */
function svgLine(x1: number, y1: number, x2: number, y2: number): TemplateResult {
  return html`<line x1=${x1} y1=${y1} x2=${x2} y2=${y2} />`;
}

declare global {
  interface HTMLElementTagNameMap {
    "solar-sanity-forecast-card": SolarSanityForecastCard;
  }
}
