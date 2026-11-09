/**
 * Tomorrow's forecast as it stands, and what this roof is expected to make of it.
 *
 * One curve per provider: its latest forecast for tomorrow, read from the
 * archive the integration rewrites on every capture. That is the same figure
 * as the "Forecast tomorrow" sensor and the one the battery outlook projects
 * with. Until 0.31.0 this card drew the day-ahead archive instead, whose rows
 * for a day two days out are the first ones seen and never revised, and showed
 * 48.2 kWh for a Monday the sensor beside it put at 34.6.
 *
 * Under each total, when the integration has one, a second figure: the same
 * forecast moved by what this roof has delivered against that provider, read
 * from the `expected_tomorrow_corrected` sensor's factor. Multiplied here, from
 * the panel's own total, rather than copied from the sensor's state, because
 * this card's "tomorrow" is the browser's day and the sensor's is Home
 * Assistant's.
 *
 * What it does not show, and why:
 *
 *  - **A score.** The day-ahead archive is what scoring reads, and the
 *    `<provider> accuracy` sensor carries that figure and its reasons. A
 *    second, shorter version of it here would only disagree with the first.
 *  - **Forecast against actual, as a shortfall.** On a system whose generation
 *    is measured before the inverter, or which exports through a meter nobody
 *    reads, the gap between a forecast and a sensor is not forecast error.
 *  - **Anything in currency.** Never, anywhere in this product.
 */

import { LitElement, css, html, nothing, svg, type SVGTemplateResult, type TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import { areaPath, hourLabels, linePath, niceTicks, round, scaleLinear } from "./chart";
import {
  loadCurrentForecast,
  peak,
  roofFactor,
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
  LovelaceCardEditor,
} from "./types/hass";

/** Chart geometry, in user units. The viewBox scales it to whatever fits. */
/**
 * How long a failed read is believed before the card tries again.
 *
 * Long enough that a genuinely disabled recorder is not polled at the rate
 * `hass` is reassigned, short enough that a momentary blip is not treated as
 * the state of the world for the rest of the day.
 */
const RETRY_AFTER_MS = 5 * 60_000;

/**
 * How long a forecast on screen is kept before it is read again.
 *
 * The integration captures every thirty minutes (`FORECAST_CAPTURE_INTERVAL`)
 * and the provider revises tomorrow through the day. Reading only once a day,
 * as this card did while it drew a series that never changes, would let a wall
 * tablet drift from the sensor beside it by the evening.
 */
const REFRESH_AFTER_MS = 30 * 60_000;

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
  /** The archive the hours came from, which the roof figure is matched on. */
  latestId: string;
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
    latestId: day.latestId,
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
  /** When the last failure happened, so the latch can expire on its own. */
  private _failedAt = 0;
  /** A load in progress, so a burst of hass updates does not start five. */
  private _inflight?: Promise<void>;
  /** When what is on screen was read, so it can be read again. */
  private _loadedAt = 0;

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

    // Two ways out of a failure, because the first one alone never fires.
    //
    // A new lifecycle phase deserves another try: the usual way to see the
    // recorder refuse is a restart. But `_load` is only reachable past the
    // RUNNING gate below, so every failure is recorded during RUNNING and this
    // comparison is always "RUNNING" against "RUNNING". Written to release the
    // latch, it never released it, and the card said "cannot read the record"
    // until midnight on a recorder that had been fine seconds later.
    //
    // So also let it expire. A websocket blip — a laptop waking, wifi roaming,
    // the recorder reloading — rejects the request in flight and is over before
    // the next reassignment, of which there are dozens a minute. Nothing about
    // that deserves to be believed for sixteen hours.
    const phase = this.hass.config.state;
    if (phase !== this._failedDuring) {
      this._failed = false;
    } else if (this._failed && Date.now() - this._failedAt > RETRY_AFTER_MS) {
      this._failed = false;
    }
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

    if (this._inflight) return;
    if (this._days === undefined) {
      if (!this._failed) void this._load();
    } else if (Date.now() - this._loadedAt > REFRESH_AFTER_MS) {
      // What is on screen stays there while it is read again.
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
      const days = await loadCurrentForecast(this.hass, day);
      // The day may have moved while this was in flight. A request started at
      // 23:59:59 answers about the day that has just become today, and
      // publishing it would leave the card showing yesterday's forecast under
      // the word "Tomorrow" until something else happened to reload it — which,
      // on a card that only loads when `_days` is undefined, is nothing.
      if (day.getTime() !== this._day.getTime()) return;
      this._days = this._config?.provider
        ? days.filter((d) => d.statisticId === this._config!.provider)
        : days;
      this._loadedAt = Date.now();
      this._failedDuring = undefined;
    } catch {
      // A failed refresh leaves what is already on screen alone: half an hour
      // old is still the forecast, and a blip should not replace it with an
      // apology. It is tried again after the same interval.
      if (this._days !== undefined) {
        this._loadedAt = Date.now();
        return;
      }
      // The recorder can be disabled, and a card is not the place to explain a
      // stack trace. What the reader needs is one sentence and no red box.
      this._failed = true;
      this._failedAt = Date.now();
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
        "No forecast for tomorrow yet",
        "Solar Sanity records each provider's forecast every half hour, so tomorrow appears with the next one the provider publishes.",
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
            The provider's latest forecast, which it revises through the day.
            What was forecast a day ahead is kept separately, to compare with
            what happens.
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
          <span class="total"><bdi>${panel.kwh.toFixed(1)} kWh</bdi></span>
        </div>
        ${this._roof(panel)}
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
            (tick) => svg`<text class="tick" x="0" y=${panel.tickY(tick) + 4}>${tick}</text>`,
          )}
          ${panel.labels.map((mark) =>
            mark.text
              ? svg`<text class="tick" x=${mark.x} y=${HEIGHT - 6}>${mark.text}</text>`
              : nothing,
          )}
        </svg>
        <!-- The same numbers, for anyone who cannot see the shape. -->
        <div class="sr-only">
        <table>
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
        </div>
      </section>
    `;
  }

  /**
   * What this roof is expected to make of the forecast, when the integration
   * has learned it. Nothing at all otherwise: an empty row would read as zero.
   */
  private _roof(panel: Panel): TemplateResult | typeof nothing {
    const roof = roofFactor(this.hass, panel.latestId);
    if (!roof) return nothing;
    const share = Math.round(roof.factor * 100);
    const over = roof.days ? ` over the last ${roof.days} days` : "";
    return html`
      <div class="heading roof">
        <span class="provider">From your roof</span>
        <span class="expect"><bdi>≈ ${(panel.kwh * roof.factor).toFixed(1)} kWh</bdi></span>
      </div>
      <p class="why">
        ${roof.byKindOfDay
          ? html`On days forecast like this one, your roof has delivered about
              <bdi>${share}%</bdi> of the forecast${over}.`
          : html`Your roof has delivered about <bdi>${share}%</bdi> of this forecast${over}.`}
      </p>
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
    .total,
    .expect {
      color: var(--primary-text-color);
      font-size: var(--ha-font-size-l, 1rem);
      font-weight: var(--ha-font-weight-medium, 500);
      font-variant-numeric: tabular-nums;
    }
    .expect {
      font-size: var(--ha-font-size-m, 0.875rem);
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
