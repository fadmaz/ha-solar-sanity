/**
 * Tomorrow as this roof is expected to make it.
 *
 * One curve per provider: its latest forecast for tomorrow, read from the
 * archive the integration rewrites on every capture, the forecast the battery
 * outlook projects with. Until 0.31.0 this card drew the day-ahead archive
 * instead, whose rows for a day two days out are the first ones seen and never
 * revised, and showed 48.2 kWh for a Monday the provider then put at 34.6.
 *
 * When the integration has learned how much of that provider's forecast this
 * roof delivers, the curve and the total are corrected by it, read from the
 * `expected_tomorrow_corrected` sensor's factor, and the forecast for an ideal
 * roof is not shown at all: since 0.33.0, at the reference installation's
 * owner's request, because the provider's own sensors already show it.
 * Multiplied here rather than copied from the sensor's state, so the curve
 * and total use exactly the same corrected hours in Home Assistant's zone.
 * Until there is a factor, the provider's forecast, and a line saying so.
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

import { languageOf, number, t, validateLanguage, type LanguageSetting } from "./i18n";
import { qualityPanel, qualityStyles } from "./quality";

import { areaPath, hourLabels, linePath, niceTicks, round, scaleLinear } from "./chart";
import {
  loadCurrentForecast,
  dayKey,
  hourLabel,
  safeTimeZone,
  peak,
  roofFactor,
  tomorrow,
  total,
  type Hour,
  type ProviderDay,
  type RoofFactor,
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
  language?: LanguageSetting;
}

interface Panel {
  label: string;
  /** The archive the hours came from, which the roof figure is matched on. */
  latestId: string;
  /** What the hours were corrected by, or nothing while uncorrected. */
  roof?: RoofFactor;
  hours: Hour[];
  kwh: number;
  line: string;
  area: string;
  ticks: number[];
  tickY: (value: number) => number;
  labels: Array<{ x: number; text: string }>;
}

/** Everything the card needs to draw one provider's day, corrected when it can be. */
export function buildPanel(day: ProviderDay, roof?: RoofFactor, timeZone?: string): Panel {
  const hours = roof
    ? day.hours.map((hour) => ({ ...hour, kwh: hour.kwh * roof.factor }))
    : day.hours;
  const top = niceTicks(peak(hours)).at(-1) ?? 1;

  const x = scaleLinear([0, Math.max(hours.length - 1, 1)], [PAD_START, WIDTH - PAD_END]);
  const y = scaleLinear([0, top], [HEIGHT - PAD_BOTTOM, PAD_TOP]);
  const points = hours.map((hour, index) => [x(index), y(hour.kwh)] as const);

  return {
    label: day.label,
    latestId: day.latestId,
    roof,
    hours,
    kwh: total(hours),
    line: linePath(points),
    area: areaPath(points, HEIGHT - PAD_BOTTOM),
    ticks: niceTicks(peak(hours)),
    tickY: (value: number) => round(y(value)),
    labels: hourLabels(hours.map((hour) => hour.start), 6,
      (when) => hourLabel(when, timeZone)).map((text, index) => ({
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
  private _timeZone = safeTimeZone();
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
    validateLanguage(config.language);
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
    const zone = safeTimeZone(this.hass.config.time_zone);
    const wanted = tomorrow(new Date(), zone);
    if (wanted.getTime() !== this._day.getTime() || zone !== this._timeZone) {
      this._timeZone = zone;
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
    this._inflight = this._loadOnce(this._day, this._timeZone).finally(() => {
      this._inflight = undefined;
      // A zone/day change can discard the request while it is in flight.
      // Load its replacement even if no further entity state changes arrive.
      if (this._days === undefined && !this._failed && this.hass?.config.state === "RUNNING") {
        void this._load();
      }
    });
    return this._inflight;
  }

  private async _loadOnce(day: Date, timeZone: string): Promise<void> {
    if (!this.hass) return;
    try {
      const days = await loadCurrentForecast(this.hass, day, timeZone);
      // The day may have moved while this was in flight. A request started at
      // 23:59:59 answers about the day that has just become today, and
      // publishing it would leave the card showing yesterday's forecast under
      // the word "Tomorrow" until something else happened to reload it — which,
      // on a card that only loads when `_days` is undefined, is nothing.
      if (day.getTime() !== this._day.getTime() || timeZone !== this._timeZone) return;
      this._days = this._config?.provider
        ? days.filter((d) => d.statisticId === this._config!.provider)
        : days;
      this._loadedAt = Date.now();
      this._failedDuring = undefined;
    } catch {
      if (day.getTime() !== this._day.getTime() || timeZone !== this._timeZone) return;
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

  private get _language() { return languageOf(this.hass?.language, this._config?.language); }

  private _t(source: string, values: Record<string, string | number> = {}): string {
    return t(this._language, source, values);
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

    const panels = this._days.map((day) =>
      buildPanel(day, roofFactor(this.hass, day.latestId), this._timeZone));
    const withData = panels.filter((panel) => panel.hours.length > 1);

    if (withData.length === 0) {
      return this._note(
        "No forecast for tomorrow yet",
        "Solar Sanity records each provider's forecast every half hour, so tomorrow appears with the next one the provider publishes.",
      );
    }

    return html`
      <ha-card>
        <div class="root" lang=${this._language} dir=${this._language === "ar" ? "rtl" : "ltr"}>
          <header>
            <h2>${this._t(this._heading())}</h2>
            <p class="when">${this._dayLabel()}</p>
          </header>
          ${withData.map((panel) => this._panel(panel))}
          <p class="footnote">${this._t("Latest forecast, revised through the day. {correction}The day-ahead forecast is kept separately for comparison.", {
            correction: withData.some((panel) => panel.roof) ? this._t("Corrected for your roof. ") : "",
          })}</p>
          ${qualityPanel(this.hass, this._language, undefined, withData.map((panel) => panel.latestId))}
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
    const now = new Date();
    const key = dayKey(this._day, this._timeZone);
    if (key === dayKey(tomorrow(now, this._timeZone), this._timeZone)) return "Tomorrow";
    if (key === dayKey(now, this._timeZone)) return "Today";
    return "Forecast";
  }

  private _dayLabel(): string {
    return this._day.toLocaleDateString(this._language, {
      weekday: "long",
      day: "numeric",
      month: "long",
      timeZone: this._timeZone,
    });
  }

  private _panel(panel: Panel): TemplateResult {
    const when = this._language === "en" ? this._heading().toLowerCase() : this._t(this._heading());
    const what = this._t(panel.roof ? "expected from your roof" : "forecast");
    const label = this._t("{provider}: {energy} kilowatt hours {what} for {day}", {provider: panel.label, energy: number(panel.kwh, this._language, 1), what, day: when});
    return html`
      <section class="panel">
        <div class="heading">
          <span class="provider">${panel.label}</span>
          <span class="total"><bdi>${number(panel.kwh, this._language, 1)} kWh</bdi></span>
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
            (tick) => svg`<text class="tick" x="0" y=${panel.tickY(tick) + 4}>${number(tick, this._language)}</text>`,
          )}
          ${panel.labels.map((mark) =>
            mark.text
              ? svg`<text class="tick" x=${mark.x} y=${HEIGHT - 6}>${this._language === "ar" ? mark.text.replace(/\d+/g, (n) => number(Number(n), "ar")) : mark.text}</text>`
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
                <th scope="row">${this._hour(hour.start)}</th>
                <td>${number(hour.kwh, this._language, 2)} kWh</td>
              </tr>`,
            )}
          </tbody>
        </table>
        </div>
      </section>
    `;
  }

  /**
   * What the total and the curve were corrected by, or that they were not.
   * The figure for an ideal roof is never printed beside the corrected one.
   */
  private _roof(panel: Panel): TemplateResult {
    const roof = panel.roof;
    if (!roof) {
      return html`<p class="why">${this._t("As the provider forecasts it, not yet corrected for your roof.")}</p>`;
    }
    const share = number(Math.round(roof.factor * 100), this._language);
    const over = roof.days ? this._t(" over the last {days} days", {days: number(roof.days, this._language)}) : "";
    return html`<p class="why">${this._t(roof.byKindOfDay
      ? "Corrected for your roof: on days forecast like this one, it has delivered about {share}% of the forecast{over}."
      : "Corrected for your roof: it has delivered about {share}% of this forecast{over}.", {share, over})}</p>`;
  }

  private _hour(when: Date): string {
    return new Intl.DateTimeFormat(this._language, {timeZone: this._timeZone,
      hour: "2-digit", minute: "2-digit", hourCycle: "h23"}).format(when);
  }

  private _note(headline: string, body: string): TemplateResult {
    return html`
      <ha-card>
        <div class="root" lang=${this._language} dir=${this._language === "ar" ? "rtl" : "ltr"}>
          <h2>${this._t(headline)}</h2>
          <p class="body">${this._t(body)}</p>
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
