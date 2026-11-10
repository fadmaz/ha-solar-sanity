/**
 * The battery outlook as the card reads it: which sensors belong to which
 * installation, what they say, the one sentence worth leading with, and the
 * shape of the line.
 *
 * Pure, like `forecast-data.ts`. Nothing here touches the DOM, so all of it is
 * tested as values. Time is formatted through an injected `Clock`, in Home
 * Assistant's time zone rather than the browser's, because "full at 14:35"
 * means the clock on the wall of the house, not of the phone in a hotel.
 */

import { languageOf, number, t, type UiLanguage } from "./i18n";
import type { HassEntity, HomeAssistant } from "./types/hass";
import { tomorrow } from "./forecast-data";

export const PLATFORM = "solar_sanity";

/** The outlook's translation keys. Literal on the Python side (`sensor.py`). */
export const OUTLOOK_KEYS = [
  "battery_full_at",
  "battery_reserve_at",
  "battery_lowest_before_sunrise",
  "safe_average_load",
  "spare_solar",
  "battery_usable_energy",
  "expected_use_tonight",
  "spare_solar_power",
] as const;
export type OutlookKey = (typeof OUTLOOK_KEYS)[number];

/** One config entry of this integration: its device, and its outlook sensors. */
export interface Installation {
  deviceId: string;
  /** Entity ids by key. Empty when the battery outlook is not set up there. */
  outlook: Partial<Record<OutlookKey, string>>;
}

/**
 * Every installation, with or without an outlook, in a stable order.
 *
 * Found through the entity registry by platform and translation key, never by
 * entity id: ids are the user's to rename, and a card that loses its sensors
 * when somebody tidies their entity names is a card that breaks on a Sunday.
 * One device per config entry (`entity.py`), so the device is the installation.
 */
export function findInstallations(hass: HomeAssistant | undefined): Installation[] {
  const byDevice = new Map<string, Installation>();
  for (const entry of Object.values(hass?.entities ?? {})) {
    if (entry.platform !== PLATFORM || !entry.device_id) continue;
    let found = byDevice.get(entry.device_id);
    if (!found) {
      found = { deviceId: entry.device_id, outlook: {} };
      byDevice.set(entry.device_id, found);
    }
    const key = entry.translation_key;
    if (key && (OUTLOOK_KEYS as readonly string[]).includes(key)) {
      found.outlook[key as OutlookKey] = entry.entity_id;
    }
  }
  return [...byDevice.values()].sort((a, b) => a.deviceId.localeCompare(b.deviceId));
}

export function hasOutlook(installation: Installation): boolean {
  return Boolean(installation.outlook.battery_lowest_before_sunrise);
}

export interface Point {
  at: Date;
  soc: number;
}

/**
 * The power sensors the card shows live, from the lowest-battery sensor's
 * `power_sources` attribute: entity ids, read straight from `hass.states`.
 */
export interface PowerSources {
  solar?: string;
  house?: string;
  grid?: string;
  battery?: string;
  /** Whether the battery's sensor reads positive while it charges. */
  batteryChargingPositive: boolean;
}

/** What the outlook sensors say, read once per render. */
export interface Outlook {
  /** Every outlook sensor is unavailable: the charge level is not reporting. */
  unavailable: boolean;
  /** Why there is no projection at all, when there is none. */
  reason: string | null;
  reasonCode?: string;
  socPct: number | null;
  forecastUsed: boolean;
  fullAt: Date | null;
  reserveAt: Date | null;
  lowestPct: number | null;
  lowestAt: Date | null;
  nightFrom: Date | null;
  takeoverAt: Date | null;
  safeLoadKw: number | null;
  safeUntil: Date | null;
  spareKwh: number | null;
  spareFrom: Date | null;
  reservePct: number | null;
  targetPct: number | null;
  trajectory: Point[];
  /** What the battery can still give before its reserve. */
  usableKwh: number | null;
  /** What the house will use from dusk until the sun takes over. */
  tonightKwh: number | null;
  tonightUntil: Date | null;
  /** Solar power going to waste now, the battery full or at its ceiling. */
  sparePowerW: number | null;
  powerSources: PowerSources | null;
}

const NO_VALUE = new Set(["unknown", "unavailable", ""]);

function asNumber(value: unknown): number | null {
  const parsed =
    typeof value === "number"
      ? value
      : typeof value === "string" && value.trim() !== ""
        ? Number(value)
        : Number.NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

function asTime(value: unknown): Date | null {
  if (typeof value !== "string" || value === "") return null;
  const when = new Date(value);
  return Number.isNaN(when.getTime()) ? null : when;
}

function stateNumber(entity: HassEntity | undefined): number | null {
  return entity && !NO_VALUE.has(entity.state) ? asNumber(entity.state) : null;
}

function stateTime(entity: HassEntity | undefined): Date | null {
  return entity && !NO_VALUE.has(entity.state) ? asTime(entity.state) : null;
}

export function readOutlook(hass: HomeAssistant, installation: Installation): Outlook {
  const get = (key: OutlookKey): HassEntity | undefined => {
    const id = installation.outlook[key];
    return id ? hass.states[id] : undefined;
  };
  const lowest = get("battery_lowest_before_sunrise");
  const full = get("battery_full_at");
  const reserve = get("battery_reserve_at");
  const safe = get("safe_average_load");
  const spare = get("spare_solar");
  const usable = get("battery_usable_energy");
  const tonight = get("expected_use_tonight");
  const sparePower = get("spare_solar_power");
  const attrs = lowest?.attributes ?? {};

  const present = [lowest, full, reserve, safe, spare, usable, tonight, sparePower].filter(
    (entity): entity is HassEntity => entity !== undefined,
  );
  const raw = Array.isArray(attrs.trajectory) ? (attrs.trajectory as unknown[]) : [];
  const trajectory = raw.flatMap((point): Point[] => {
    const record = (point ?? {}) as Record<string, unknown>;
    const at = asTime(record.at);
    const soc = asNumber(record.soc);
    return at && soc !== null ? [{ at, soc }] : [];
  });
  const reason = typeof attrs.reason === "string" ? attrs.reason : null;
  const production = typeof attrs.production_from === "string" ? attrs.production_from : "";

  return {
    unavailable: present.length > 0 && present.every((entity) => entity.state === "unavailable"),
    // A reason with a trajectory beside it is about this one figure ("no night
    // in the forecast"); without one, nothing could be projected at all.
    reason: trajectory.length === 0 ? reason : null,
    reasonCode: typeof attrs.reason_code === "string" ? attrs.reason_code : undefined,
    socPct: asNumber(attrs.soc_pct) ?? trajectory[0]?.soc ?? null,
    forecastUsed: production.startsWith("forecast"),
    fullAt: stateTime(full),
    reserveAt: stateTime(reserve),
    lowestPct: stateNumber(lowest),
    lowestAt: asTime(attrs.at),
    nightFrom: asTime(attrs.night_from),
    takeoverAt: asTime(attrs.takeover_at),
    safeLoadKw: stateNumber(safe),
    safeUntil: asTime(safe?.attributes.until),
    spareKwh: stateNumber(spare),
    spareFrom: asTime(spare?.attributes.day_from),
    reservePct: asNumber(attrs.reserve_pct) ?? asNumber(reserve?.attributes.reserve_pct),
    targetPct: asNumber(attrs.target_pct) ?? asNumber(full?.attributes.target_pct),
    trajectory,
    usableKwh: stateNumber(usable),
    tonightKwh: stateNumber(tonight),
    tonightUntil: asTime(tonight?.attributes.until),
    sparePowerW: stateNumber(sparePower),
    powerSources: readSources(attrs.power_sources),
  };
}

function readSources(raw: unknown): PowerSources | null {
  if (typeof raw !== "object" || raw === null) return null;
  const record = raw as Record<string, unknown>;
  const id = (key: string): string | undefined =>
    typeof record[key] === "string" && record[key] !== "" ? (record[key] as string) : undefined;
  return {
    solar: id("solar"),
    house: id("house"),
    grid: id("grid"),
    battery: id("battery"),
    batteryChargingPositive: record.battery_charging_positive !== false,
  };
}

/** Watts as a person reads them: whole watts under a kilowatt, then kW. */
export function power(watts: number, language: UiLanguage = "en"): string {
  const whole = Math.round(watts);
  return Math.abs(whole) < 1000 ? `${number(whole, language)} W` : `${number(watts / 1000, language, 1)} kW`;
}

export interface FlowItem {
  label: string;
  value: string;
}

/**
 * Below this a battery's reading is called idle: a BMS reports a few watts of
 * its own housekeeping either way, and "charging 3 W" would be a false alarm.
 */
const IDLE_W = 20;

/**
 * The flow between the sun, the house, the battery and the grid, now.
 *
 * Read live from the sensors the integration named, in the units they report
 * (W or kW; anything else is left out rather than guessed). A source not
 * named, or not reporting a number, is left out, never shown as zero.
 */
export function powerFlow(hass: HomeAssistant, sources: PowerSources | null,
  language: UiLanguage = languageOf(hass.language)): FlowItem[] {
  if (!sources) return [];
  const read = (id: string | undefined): number | null => {
    if (!id) return null;
    const entity = hass.states[id];
    const value = stateNumber(entity);
    if (value === null) return null;
    const unit = entity?.attributes.unit_of_measurement;
    if (unit === "W") return value;
    if (unit === "kW") return value * 1000;
    return null;
  };
  const items: FlowItem[] = [];
  const solar = read(sources.solar);
  if (solar !== null) items.push({ label: t(language, "Solar"), value: power(solar, language) });
  const house = read(sources.house);
  if (house !== null) items.push({ label: t(language, "House"), value: power(house, language) });
  const battery = read(sources.battery);
  if (battery !== null) {
    const charging = sources.batteryChargingPositive ? battery : -battery;
    const value =
      Math.abs(charging) < IDLE_W
        ? t(language, "idle")
        : `${t(language, charging > 0 ? "charging" : "discharging")} ${power(Math.abs(charging), language)}`;
    items.push({ label: t(language, "Battery"), value });
  }
  const grid = read(sources.grid);
  if (grid !== null) items.push({ label: t(language, "Grid"), value: power(grid, language) });
  return items;
}

/** Time, formatted for the house rather than for the browser. */
export interface Clock {
  language?: UiLanguage;
  /** "14:35" */
  time(when: Date): string;
  /** The wall-clock hour, 0 to 23. */
  hour(when: Date): number;
  /** "today", "tomorrow", or a weekday. */
  day(when: Date, now: Date): string;
}

/**
 * A clock in Home Assistant's time zone.
 *
 * Falls back to the browser's zone when the name is missing or not one the
 * browser knows, rather than throwing while a dashboard renders.
 */
export function clockFor(language: string | undefined, timeZone: string | undefined): Clock {
  const make = (locale: string | undefined, options: Intl.DateTimeFormatOptions) => {
    try {
      return new Intl.DateTimeFormat(locale, { ...options, timeZone });
    } catch {
      return new Intl.DateTimeFormat(locale, options);
    }
  };
  const clock = make(language, { numberingSystem: languageOf(language) === "ar" ? "arab" : "latn", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  // Numbers the code reads back, so always Latin digits whatever the language.
  const hours = make("en-GB", { hour: "numeric", hourCycle: "h23" });
  const date = make("en-CA", { year: "numeric", month: "2-digit", day: "2-digit" });
  const weekday = make(language, { weekday: "long" });

  return {
    language: languageOf(language),
    time: (when) => clock.format(when),
    hour: (when) => Number(hours.formatToParts(when).find((part) => part.type === "hour")?.value),
    day: (when, now) => {
      const key = date.format(when);
      if (key === date.format(now)) return t(languageOf(language), "today");
      if (key === date.format(tomorrow(now, timeZone))) return t(languageOf(language), "tomorrow");
      return weekday.format(when);
    },
  };
}

/** "2 h 10 min", "45 min", "3 h". */
export function duration(milliseconds: number, language: UiLanguage = "en"): string {
  const minutes = Math.max(0, Math.round(milliseconds / 60_000));
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return t(language, "{n} min", {n: number(rest, language)});
  return rest === 0 ? t(language, "{n} h", {n: number(hours, language)})
    : t(language, "{h} h {m} min", {h: number(hours, language), m: number(rest, language)});
}

export type Tone = "good" | "warn" | "neutral";

export interface Headline {
  text: string;
  detail: string | null;
  tone: Tone;
}

/** The highest charge before the night begins, and when. */
function peakBefore(trajectory: Point[], until: Date | null): Point | null {
  let best: Point | null = null;
  for (const point of trajectory) {
    if (until && point.at > until) break;
    if (!best || point.soc > best.soc) best = point;
  }
  return best;
}

/**
 * The one sentence worth leading with.
 *
 * Chosen from what the projection says about the night and the sun, never from
 * the clock on the reader's device: "is it evening" is a question the outlook
 * has already answered with `night_from`, in the house's own time, and a
 * browser in another zone would answer it wrong.
 *
 * In order: a battery that will run out before the sun is the only warning;
 * once the night has begun, what matters is getting through it; by day, when
 * it fills; and if a forecast says it will not, say so.
 */
export function headline(outlook: Outlook, now: Date, clock: Clock): Headline {
  const language = clock.language ?? "en";
  const at = (when: Date) => clock.time(when);
  const pct = (value: number) => `${number(Math.round(value), language)}%`;
  const inFuture = (when: Date) => when.getTime() > now.getTime();

  if (outlook.reserveAt) {
    const parts: string[] = [];
    if (inFuture(outlook.reserveAt)) parts.push(t(language, "in {duration}", {duration: duration(outlook.reserveAt.getTime() - now.getTime(), language)}));
    if (outlook.takeoverAt) parts.push(t(language, "the sun takes over at {time}", {time: at(outlook.takeoverAt)}));
    return {
      text: t(language, "Reaches the reserve at {time}", {time: at(outlook.reserveAt)}),
      detail: parts.length ? capitalise(parts.join(language === "ar" ? "، " : ", ")) : null,
      tone: "warn",
    };
  }

  const night = outlook.nightFrom !== null && !inFuture(outlook.nightFrom);
  if (night && outlook.takeoverAt) {
    return {
      text: t(language, "Lasts until the sun, at {time}", {time: at(outlook.takeoverAt)}),
      detail:
        outlook.lowestPct !== null
          ? t(language, "Lowest {pct}{at}", {pct: pct(outlook.lowestPct), at: outlook.lowestAt ? t(language, " at {time}", {time: at(outlook.lowestAt)}) : ""})
          : null,
      tone: "good",
    };
  }

  if (outlook.fullAt) {
    return inFuture(outlook.fullAt)
      ? {
          text: t(language, "Full at {time}", {time: at(outlook.fullAt)}),
          detail: t(language, "In {duration}", {duration: duration(outlook.fullAt.getTime() - now.getTime(), language)}),
          tone: "good",
        }
      : { text: t(language, "Full"), detail: t(language, "Since {time}", {time: at(outlook.fullAt)}), tone: "good" };
  }

  if (outlook.forecastUsed && !night) {
    const peak = peakBefore(outlook.trajectory, outlook.nightFrom);
    if (peak) {
      return {
        text: t(language, "Will not fill today"),
        detail: t(language, "Peaks at {pct} at {time}", {pct: pct(peak.soc), time: at(peak.at)}),
        tone: "neutral",
      };
    }
  }

  if (outlook.lowestPct !== null) {
    return {
      text: t(language, "Lowest tonight {pct}", {pct: pct(outlook.lowestPct)}),
      detail: outlook.lowestAt ? t(language, "At {time}", {time: at(outlook.lowestAt)}) : null,
      tone: "neutral",
    };
  }

  return {
    text: outlook.socPct !== null ? t(language, "Battery at {pct}", {pct: pct(outlook.socPct)}) : t(language, "Battery outlook"),
    detail: null,
    tone: "neutral",
  };
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export interface Figure {
  label: string;
  value: string;
  sub: string | null;
}

/** The six numbers under the headline. A dash, never a zero, for no answer. */
export function figures(outlook: Outlook, now: Date, clock: Clock): Figure[] {
  const language = clock.language ?? "en";
  const dash = "—";
  return [
    {
      label: t(language, "Safe load tonight"),
      value: outlook.safeLoadKw !== null ? `${number(outlook.safeLoadKw, language, 2)} kW` : dash,
      sub: outlook.safeUntil ? t(language, "until {time}", {time: clock.time(outlook.safeUntil)}) : null,
    },
    {
      label: t(language, "Lowest before sunrise"),
      value: outlook.lowestPct !== null ? `${number(Math.round(outlook.lowestPct), language)}%` : dash,
      sub: outlook.lowestAt ? t(language, "at {time}", {time: clock.time(outlook.lowestAt)}) : null,
    },
    {
      label: t(language, "Spare sun"),
      value: outlook.spareKwh !== null ? `${number(outlook.spareKwh, language, 1)} kWh` : dash,
      sub: outlook.spareFrom ? clock.day(outlook.spareFrom, now) : null,
    },
    {
      label: t(language, "Usable now"),
      value: outlook.usableKwh !== null ? `${number(outlook.usableKwh, language, 1)} kWh` : dash,
      sub: outlook.reservePct !== null ? t(language, "above {pct}", {pct: `${number(Math.round(outlook.reservePct), language)}%`}) : null,
    },
    {
      label: t(language, "Tonight's use"),
      value: outlook.tonightKwh !== null ? `${number(outlook.tonightKwh, language, 1)} kWh` : dash,
      sub: outlook.tonightUntil ? t(language, "until {time}", {time: clock.time(outlook.tonightUntil)}) : null,
    },
    {
      label: t(language, "Spare sun now"),
      value: outlook.sparePowerW !== null ? power(outlook.sparePowerW, language) : dash,
      sub: null,
    },
  ];
}

/** Where things sit on the chart, as fractions of its width and height. */
export interface ChartShape {
  /** SVG path data in a 0..WIDTH by 0..HEIGHT box. */
  line: string;
  area: string;
  night: { from: number; to: number } | null;
  /** Fractions from the top, 0 to 1. */
  reserve: number | null;
  target: number | null;
  start: { x: number; y: number };
  marks: { kind: "full" | "reserve"; x: number; y: number }[];
  hours: { x: number; label: string }[];
}

export const CHART_WIDTH = 600;
export const CHART_HEIGHT = 120;

/** The charge at a moment, read off the line by interpolation. */
export function socAt(trajectory: Point[], when: Date): number | null {
  const t = when.getTime();
  for (let index = 1; index < trajectory.length; index++) {
    const before = trajectory[index - 1]!;
    const after = trajectory[index]!;
    if (t < before.at.getTime() || t > after.at.getTime()) continue;
    const span = after.at.getTime() - before.at.getTime();
    const share = span > 0 ? (t - before.at.getTime()) / span : 0;
    return before.soc + share * (after.soc - before.soc);
  }
  return null;
}

/**
 * The projection as a shape. `null` when there is not enough of it to draw.
 *
 * The y axis is always 0 to 100: a battery chart that rescales to its data
 * makes a two-point dip look like a cliff. Hours are labelled every six on the
 * house's clock, so the marks sit on 00, 06, 12 and 18 whatever the zone.
 */
export function chartShape(outlook: Outlook, clock: Clock): ChartShape | null {
  const points = outlook.trajectory;
  if (points.length < 2) return null;
  const first = points[0]!.at.getTime();
  const last = points[points.length - 1]!.at.getTime();
  if (last <= first) return null;

  const fx = (when: Date) => Math.min(Math.max((when.getTime() - first) / (last - first), 0), 1);
  const fy = (soc: number) => 1 - Math.min(Math.max(soc, 0), 100) / 100;
  const round2 = (value: number) => Math.round(value * 100) / 100;
  const px = (when: Date) => round2(fx(when) * CHART_WIDTH);
  const py = (soc: number) => round2(fy(soc) * CHART_HEIGHT);

  const coords = points.map((point) => `${px(point.at)},${py(point.soc)}`);
  const line = `M${coords.join(" L")}`;
  const area = `${line} L${px(points[points.length - 1]!.at)},${CHART_HEIGHT} L0,${CHART_HEIGHT} Z`;

  let night: ChartShape["night"] = null;
  if (outlook.nightFrom && outlook.nightFrom.getTime() < last) {
    const to = outlook.takeoverAt ?? points[points.length - 1]!.at;
    night = { from: fx(outlook.nightFrom), to: fx(to) };
  }

  const marks: ChartShape["marks"] = [];
  for (const [kind, when] of [
    ["full", outlook.fullAt],
    ["reserve", outlook.reserveAt],
  ] as const) {
    if (!when || when.getTime() < first || when.getTime() > last) continue;
    const soc = socAt(points, when);
    if (soc !== null) marks.push({ kind, x: fx(when), y: fy(soc) });
  }

  const hours: ChartShape["hours"] = [];
  const step = 3_600_000;
  for (let t = Math.ceil(first / step) * step; t <= last; t += step) {
    const when = new Date(t);
    const hour = clock.hour(when);
    if (hour % 6 === 0) hours.push({ x: fx(when), label: String(hour).padStart(2, "0") });
  }

  return {
    line,
    area,
    night,
    reserve: outlook.reservePct !== null ? fy(outlook.reservePct) : null,
    target: outlook.targetPct !== null ? fy(outlook.targetPct) : null,
    start: { x: 0, y: fy(points[0]!.soc) },
    marks,
    hours,
  };
}
