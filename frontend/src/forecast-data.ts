/**
 * Reading the day-ahead forecast archive, without a bespoke API.
 *
 * The archive is written as Home Assistant *external statistics*, which means
 * Home Assistant's own `recorder/list_statistic_ids` and
 * `recorder/statistics_during_period` can already read it — both of them
 * non-admin, both of them stable. The integration needs no WebSocket command of
 * its own, and this card needs no version handshake with it.
 *
 * Only `state` is ever read. Each row's `state` is the kWh we wrote for that
 * hour; `sum` is a running total across a horizon that gets rewritten many
 * times a day, so differencing it says nothing about any hour.
 *
 * Everything here except `loadDayAhead` is pure, which is where the tests are.
 */

import type { HomeAssistant } from "./types/hass";

/** External statistic ids under this prefix hold what was forecast a day ahead. */
export const DAYAHEAD_PREFIX = "solar_sanity:dayahead_";

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

/** Midnight to midnight, in the browser's zone — which is the user's. */
export function localDayBounds(day: Date): { start: Date; end: Date } {
  const start = new Date(day.getFullYear(), day.getMonth(), day.getDate());
  const end = new Date(start);
  end.setDate(end.getDate() + 1);
  return { start, end };
}

/** Tomorrow, relative to a given moment. */
export function tomorrow(now: Date): Date {
  const day = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  day.setDate(day.getDate() + 1);
  return day;
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
export function hoursOn(hours: readonly Hour[], day: Date): Hour[] {
  const { start, end } = localDayBounds(day);
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
  statisticId: string;
  label: string;
  hours: Hour[];
}

/**
 * Every day-ahead archive's forecast for one local day.
 *
 * Two round trips, both to Home Assistant's own commands. Returns an empty list
 * when there is no archive at all, which the card must distinguish from an
 * archive that holds nothing for this particular day.
 */
export async function loadDayAhead(hass: HomeAssistant, day: Date): Promise<ProviderDay[]> {
  const all = await hass.callWS<ArchiveMeta[]>({
    type: "recorder/list_statistic_ids",
    statistic_type: "sum",
  });

  const archives = dayAheadArchives(all ?? []);
  // `statistic_ids` is required and must hold at least one id, so an empty
  // archive list has to short-circuit rather than ask for nothing.
  if (archives.length === 0) return [];

  const { start, end } = localDayBounds(day);
  const rows = await hass.callWS<Record<string, StatisticRow[]>>({
    type: "recorder/statistics_during_period",
    start_time: start.toISOString(),
    end_time: end.toISOString(),
    statistic_ids: archives.map((meta) => meta.statistic_id),
    period: "hour",
    types: ["state"],
    units: { energy: "kWh" },
  });

  return archives.map((meta) => ({
    statisticId: meta.statistic_id,
    label: providerLabel(meta),
    hours: hoursOn(toHours(rows?.[meta.statistic_id]), day),
  }));
}
