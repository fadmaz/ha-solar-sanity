/** Local forecast dates, legacy provider IDs, and live corrected hours. */

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

export function hourLabel(when: Date, timeZone?: string, showOffset = false): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: safeTimeZone(timeZone), hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    ...(showOffset ? { timeZoneName: "shortOffset" } : {}),
  }).format(when);
}

/** Sum only the hours supplied by the corrected sensor. */
export function total(hours: readonly Hour[]): number {
  return hours.reduce((sum, hour) => sum + hour.kwh, 0);
}

export function peak(hours: readonly Hour[]): number {
  return hours.reduce((most, hour) => Math.max(most, hour.kwh), 0);
}

export interface CorrectedForecast {
  latestId: string;
  label: string;
  factor: number;
  days?: number;
  byKindOfDay: boolean;
  validUntil: number;
  hours: Hour[];
}

interface ForecastView {
  forecasts: CorrectedForecast[];
  reasons: string[];
}

export function correctedForecasts(
  hass: HomeAssistant, day: Date, zone: string, now: number, provider?: string,
): ForecastView {
  const matching = new Map<string, typeof hass.states[string][]>();
  const reasons = new Set<string>();
  for (const entity of Object.values(hass.entities ?? {})) {
    if (entity.platform !== PLATFORM || entity.translation_key !== CORRECTED_KEY) continue;
    const state = hass.states[entity.entity_id];
    if (!state) continue;
    const latestId = state.attributes.forecast_statistic_id;
    if (provider && latestId !== latestIdFor(provider)) continue;
    if (typeof latestId !== "string") {
      if (typeof state.attributes.reason === "string") reasons.add(state.attributes.reason);
      continue;
    }
    const group = matching.get(latestId) ?? [];
    group.push(state);
    matching.set(latestId, group);
  }

  const forecasts: CorrectedForecast[] = [];
  const wantedDay = dayKey(day, zone);
  const wantedDate = new Date(wantedDay).toISOString().slice(0, 10);
  for (const [latestId, group] of matching) {
    if (group.length !== 1) {
      reasons.add("Several installations share this provider. Its corrected forecast cannot be assigned to one roof.");
      continue;
    }
    const state = group[0]!;
    const attrs = state.attributes;
    if (state.state === "" || !Number.isFinite(Number(state.state))) {
      reasons.add(typeof attrs.reason === "string" ? attrs.reason : "Waiting for a corrected forecast.");
      continue;
    }
    const validUntil = typeof attrs.forecast_valid_until === "string" ? Date.parse(attrs.forecast_valid_until) : NaN;
    if (!Number.isFinite(validUntil) || now > validUntil || attrs.forecast_for_date !== wantedDate) {
      reasons.add("Waiting for a fresh corrected forecast for this day.");
      continue;
    }
    const factor = attrs.applied_factor ?? attrs.factor;
    if (typeof factor !== "number" || !Number.isFinite(factor) || factor <= 0) continue;
    const hours = new Map<number, Hour>();
    if (Array.isArray(attrs.corrected_hours)) {
      for (const item of attrs.corrected_hours) {
        if (typeof item !== "object" || item === null) continue;
        const {start, kwh} = item as Record<string, unknown>;
        if (typeof start !== "string" || typeof kwh !== "number" || !Number.isFinite(kwh) || kwh < 0) continue;
        const instant = new Date(start);
        if (Number.isFinite(instant.getTime()) && dayKey(instant, zone) === wantedDay) {
          hours.set(instant.getTime(), {start: instant, kwh});
        }
      }
    }
    if (!hours.size) {
      reasons.add("No corrected hours are available for this day.");
      continue;
    }
    forecasts.push({
      latestId, factor, validUntil,
      label: typeof attrs.provider === "string" ? attrs.provider : "Solar forecast",
      days: typeof attrs.days === "number" ? attrs.days : undefined,
      byKindOfDay: attrs.basis === "learned",
      hours: [...hours.values()].sort((a, b) => a.start.getTime() - b.start.getTime()),
    });
  }
  return {forecasts, reasons: [...reasons]};
}
