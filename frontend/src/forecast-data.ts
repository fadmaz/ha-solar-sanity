/**
 * Reading the forecast archives, without a bespoke API.
 *
 * Each provider has two archives, written as Home Assistant *external
 * statistics*, so Home Assistant's own `recorder/list_statistic_ids` and
 * `recorder/statistics_during_period` can already read them — both non-admin,
 * both stable. The integration needs no WebSocket command of its own, and this
 * card needs no version handshake with it.
 *
 *  - `solar_sanity:dayahead_<key>`: each hour as first seen at least twelve
 *    hours ahead, and never revised. What scoring and learning read. It is
 *    also how the providers are *found*: its metadata carries the provider's
 *    name, and the card's `provider` option has always stored this id.
 *  - `solar_sanity:forecast_<key>`: each hour's latest revision, rewritten on
 *    every capture. What the card *draws*. For an hour that has passed it
 *    holds a figure issued minutes before, which is why nothing grades
 *    against it; for tomorrow, every hour of which is still to come, it is
 *    simply the forecast as it stands. Until 0.31.0 the card drew the
 *    day-ahead series instead and showed 48.2 kWh for a Monday whose current
 *    forecast, on the sensor beside it, was 34.6: the day-ahead rows for a day
 *    two days out are the first ones seen, about two days old.
 *
 * Only `state` is ever read. Each row's `state` is the kWh we wrote for that
 * hour; `sum` is a running total across a horizon that gets rewritten many
 * times a day, so differencing it says nothing about any hour.
 *
 * Everything here except `loadCurrentForecast` is pure, which is where the
 * tests are.
 */

import type { HomeAssistant } from "./types/hass";

/** External statistic ids under this prefix hold what was forecast a day ahead. */
export const DAYAHEAD_PREFIX = "solar_sanity:dayahead_";

/** And under this one, each hour's latest revision. */
export const FORECAST_PREFIX = "solar_sanity:forecast_";

/** The integration this card's sensors belong to. */
const PLATFORM = "solar_sanity";

/** The sensor that says what this roof delivers against a provider's forecast. */
const CORRECTED_KEY = "expected_tomorrow_corrected";

/** The latest-revision archive belonging to a day-ahead one. */
export function latestIdFor(dayAheadId: string): string {
  return dayAheadId.startsWith(DAYAHEAD_PREFIX)
    ? FORECAST_PREFIX + dayAheadId.slice(DAYAHEAD_PREFIX.length)
    : dayAheadId;
}

/** The suffix the integration puts on its day-ahead metadata name. */
const NAME_SUFFIX = " forecast, a day ahead";

/** One provider's archive, as the recorder describes it. */
export interface ArchiveMeta {
  statistic_id: string;
  name?: string | null;
}

/** One hour of forecast energy. */
export interface Hour {
  start: Date;
  kwh: number;
}

/** What the recorder returns per row. `start` is epoch milliseconds. */
interface StatisticRow {
  start: number;
  state?: number | null;
}

/** The day-ahead archives among everything the recorder knows about. */
export function dayAheadArchives(all: readonly ArchiveMeta[]): ArchiveMeta[] {
  return all
    .filter((meta) => meta.statistic_id.startsWith(DAYAHEAD_PREFIX))
    .sort((a, b) => a.statistic_id.localeCompare(b.statistic_id));
}

/**
 * A provider's name, for a heading.
 *
 * Falls back to the id rather than to a blank: an unfamiliar id at least tells
 * the reader which archive they are looking at, where an empty heading tells
 * them the card is broken.
 */
export function providerLabel(meta: ArchiveMeta): string {
  const name = meta.name?.trim();
  if (!name) return meta.statistic_id.slice(DAYAHEAD_PREFIX.length);
  return name.endsWith(NAME_SUFFIX) ? name.slice(0, -NAME_SUFFIX.length) : name;
}

/** The house's zone, with the browser's as a fallback for a missing/invalid one. */
export function safeTimeZone(timeZone?: string): string {
  try {
    return new Intl.DateTimeFormat("en", { timeZone }).resolvedOptions().timeZone;
  } catch {
    return new Intl.DateTimeFormat("en").resolvedOptions().timeZone;
  }
}

const calendars = new Map<string, Intl.DateTimeFormat>();
const midnights = new Map<string, number>();

/** A UTC date used only as a calendar label, never as the house's midnight. */
function calendarDay(when: Date, zone: string): number {
  let formatter = calendars.get(zone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-GB", {
      timeZone: zone, calendar: "iso8601", numberingSystem: "latn",
      year: "numeric", month: "2-digit", day: "2-digit",
    });
    calendars.set(zone, formatter);
  }
  const parts = formatter.formatToParts(when);
  const read = (type: string) => Number(parts.find((part) => part.type === type)!.value);
  return Date.UTC(read("year"), read("month") - 1, read("day"));
}

function midnight(day: number, zone: string): Date {
  const key = `${zone}:${day}`;
  let result = midnights.get(key);
  if (result === undefined) {
    // Find the first instant of the calendar day. Adding 24 hours is wrong on
    // DST changes; searching also handles zones whose midnight is skipped.
    let low = day - 36 * 3_600_000;
    let high = day + 36 * 3_600_000;
    while (high - low > 1) {
      const middle = Math.floor((low + high) / 2);
      if (calendarDay(new Date(middle), zone) < day) low = middle;
      else high = middle;
    }
    result = high;
    if (midnights.size >= 64) midnights.clear();
    midnights.set(key, result);
  }
  return new Date(result);
}

/** Midnight to midnight in the house's zone, including 23- and 25-hour days. */
export function localDayBounds(day: Date, timeZone?: string): { start: Date; end: Date } {
  const zone = safeTimeZone(timeZone);
  const calendar = calendarDay(day, zone);
  return { start: midnight(calendar, zone), end: midnight(calendar + 86_400_000, zone) };
}

/** Tomorrow, relative to a given moment. */
export function tomorrow(now: Date, timeZone?: string): Date {
  const zone = safeTimeZone(timeZone);
  return midnight(calendarDay(now, zone) + 86_400_000, zone);
}

export function dayKey(day: Date, timeZone?: string): number {
  return calendarDay(day, safeTimeZone(timeZone));
}

export function hourLabel(when: Date, timeZone?: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: safeTimeZone(timeZone), hour: "2-digit", hourCycle: "h23",
  }).format(when);
}

/**
 * Recorder rows as hours.
 *
 * A row with no `state` is dropped rather than read as zero. An hour nobody
 * forecast and an hour forecast to produce nothing are different facts, and on
 * a chart the second draws a line to the floor while the first should not draw
 * at all.
 */
export function toHours(rows: readonly StatisticRow[] | undefined): Hour[] {
  if (!rows) return [];
  return rows
    .filter((row) => typeof row.state === "number" && Number.isFinite(row.state))
    .map((row) => ({ start: new Date(row.start), kwh: row.state as number }))
    .sort((a, b) => a.start.getTime() - b.start.getTime());
}

/** Only the hours belonging to one local day. */
export function hoursOn(hours: readonly Hour[], day: Date, timeZone?: string): Hour[] {
  const { start, end } = localDayBounds(day, timeZone);
  return hours.filter((hour) => hour.start >= start && hour.start < end);
}

/** Total energy across the given hours, in kWh. */
export function total(hours: readonly Hour[]): number {
  return hours.reduce((sum, hour) => sum + hour.kwh, 0);
}

/** The largest hourly value, or zero when there is nothing. */
export function peak(hours: readonly Hour[]): number {
  return hours.reduce((most, hour) => Math.max(most, hour.kwh), 0);
}

/** One provider's day. */
export interface ProviderDay {
  /** The day-ahead archive's id: how the provider is found and configured. */
  statisticId: string;
  /** The latest-revision archive's id: where the hours were read from. */
  latestId: string;
  label: string;
  hours: Hour[];
}

/**
 * Every provider's current forecast for one local day.
 *
 * Two round trips, both to Home Assistant's own commands. Returns an empty list
 * when there is no archive at all, which the card must distinguish from an
 * archive that holds nothing for this particular day.
 */
export async function loadCurrentForecast(
  hass: HomeAssistant,
  day: Date,
  timeZone = hass.config?.time_zone,
): Promise<ProviderDay[]> {
  const all = await hass.callWS<ArchiveMeta[]>({
    type: "recorder/list_statistic_ids",
    statistic_type: "sum",
  });

  const archives = dayAheadArchives(all ?? []);
  // `statistic_ids` is required and must hold at least one id, so an empty
  // archive list has to short-circuit rather than ask for nothing.
  if (archives.length === 0) return [];

  const { start, end } = localDayBounds(day, timeZone);
  const latest = archives.map((meta) => latestIdFor(meta.statistic_id));
  const rows = await hass.callWS<Record<string, StatisticRow[]>>({
    type: "recorder/statistics_during_period",
    start_time: start.toISOString(),
    end_time: end.toISOString(),
    statistic_ids: latest,
    period: "hour",
    types: ["state"],
    units: { energy: "kWh" },
  });

  return archives.map((meta, index) => ({
    statisticId: meta.statistic_id,
    latestId: latest[index]!,
    label: providerLabel(meta),
    hours: hoursOn(toHours(rows?.[latest[index]!]), day, timeZone),
  }));
}

/** What this roof has delivered against one provider's forecast. */
export interface RoofFactor {
  hours?: Hour[];
  /** Multiplies the forecast; 0.52 means about half of it arrives. */
  factor: number;
  /** How many days that rests on. */
  days?: number;
  /**
   * True when learned by the battery outlook, which weights the days whose
   * forecast looked like tomorrow's: a sunny day and a cloudy one are not
   * corrected by the same amount.
   */
  byKindOfDay: boolean;
}

/**
 * The factor the `expected_tomorrow_corrected` sensor applies to this
 * provider, or nothing.
 *
 * Found through the entity registry, by integration and translation key, so a
 * renamed entity is still found, and matched to the panel by the archive id the
 * sensor names. Only an answer when exactly one sensor gives one: two Solar
 * Sanity installations can share a provider and learn different factors, and
 * picking either would put one house's roof under the other's forecast.
 */
export function roofFactor(
  hass: HomeAssistant | undefined,
  latestId: string,
): RoofFactor | undefined {
  const found: RoofFactor[] = [];
  for (const entry of Object.values(hass?.entities ?? {})) {
    if (entry.platform !== PLATFORM || entry.translation_key !== CORRECTED_KEY) continue;
    const state = hass?.states[entry.entity_id];
    if (!state || state.attributes.forecast_statistic_id !== latestId) continue;
    if (!Number.isFinite(Number.parseFloat(state.state))) continue;
    const factor = state.attributes.factor;
    if (typeof factor !== "number" || !Number.isFinite(factor) || factor <= 0) continue;
    const days = state.attributes.days;
    found.push({
      factor,
      hours: Array.isArray(state.attributes.corrected_hours) ? state.attributes.corrected_hours.flatMap((row: unknown) => {
        if (typeof row !== "object" || row === null) return [];
        const value = row as Record<string, unknown>;
        const start = typeof value.start === "string" ? new Date(value.start) : null;
        return start && Number.isFinite(start.getTime()) && typeof value.kwh === "number"
          && Number.isFinite(value.kwh) && value.kwh >= 0 ? [{start, kwh: value.kwh}] : [];
      }) : undefined,
      days: typeof days === "number" ? days : undefined,
      byKindOfDay: state.attributes.basis === "learned",
    });
  }
  return found.length === 1 ? found[0] : undefined;
}
