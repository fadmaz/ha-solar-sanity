/** Read descriptive performance from the integration; never grade in the browser. */
import { css, html, nothing, type TemplateResult } from "lit";

import type { HassEntity, HomeAssistant } from "./types/hass";
import { safeTimeZone } from "./forecast-data";

const METRICS = [
  ["night_lowest_mae", "Night minimum error", "percentage points"],
  ["reserve_call_accuracy", "Reserve calls", "%"],
  ["morning_fill_accuracy", "Morning fill calls", "%"],
  ["fill_time_mae", "Fill time error", "min"],
] as const;

function finite(value: unknown): number | null {
  if (typeof value !== "number" && typeof value !== "string") return null;
  if (value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function qualityPanel(hass: HomeAssistant, deviceId?: string, providerIds?: string[]): TemplateResult | typeof nothing {
  const entries = Object.values(hass.entities ?? {}).filter((e) => e.platform === "solar_sanity");
  const statuses = entries.filter((e) => e.translation_key === "collection_status"
    && (!deviceId || e.device_id === deviceId)
    && (!providerIds || entries.some((p) => p.device_id === e.device_id
      && p.translation_key === "forecast_last_captured_at"
      && providerIds.includes(String(hass.states[p.entity_id]?.attributes.forecast_statistic_id)))));
  if (statuses.length === 0) return nothing;
  const stamp = (entity?: HassEntity) => {
    const when = entity ? new Date(entity.state) : null;
    return when && !Number.isNaN(when.getTime())
      ? new Intl.DateTimeFormat("en", { timeZone: safeTimeZone(hass.config.time_zone),
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(when)
      : "—";
  };
  return html`${statuses.map((entry) => {
    const get = (key: string) => {
      const item = entries.find((e) => e.device_id === entry.device_id && e.translation_key === key);
      return item ? hass.states[item.entity_id] : undefined;
    };
    const status = hass.states[entry.entity_id];
    const title = ({ok: "OK", starting: "Starting", degraded: "Degraded", error: "Error"} as Record<string, string>)[status?.state ?? ""] ?? "Starting";
    const reason = String(status?.attributes.reason ?? "");
    const night = get("night_lowest_mae");
    const morning = get("morning_fill_accuracy");
    const count = (v: unknown) => { const n = finite(v); return n === null ? "—" : n.toFixed(0); };
    const hasSamples = finite(night?.attributes.sample_count) !== null
      && Number(night?.attributes.sample_count) > 0
      || finite(morning?.attributes.sample_count) !== null && Number(morning?.attributes.sample_count) > 0;
    const recent = night?.attributes.last_7_days as Record<string, unknown> | undefined;
    const excluded = night?.attributes.excluded as Record<string, unknown> | undefined;
    const excludedCount = excluded ? Object.values(excluded).reduce<number>((n, v) => n + (finite(v) ?? 0), 0) : null;
    const providerEntries = entries.filter((e) => e.device_id === entry.device_id
      && e.translation_key === "forecast_last_captured_at"
      && (!providerIds || providerIds.includes(String(hass.states[e.entity_id]?.attributes.forecast_statistic_id))));
    return html`<details class="quality" lang="en" dir="ltr">
      <summary><span>Accuracy and data quality</span>
        <span class="quality-status" data-state=${status?.state}>${title}</span></summary>
      <div class="quality-body">
        ${statuses.length > 1 ? html`<p>${status?.attributes.friendly_name ?? "Solar Sanity"}</p>` : nothing}
        ${reason ? html`<p>${reason}</p>` : nothing}
        ${night || morning ? html`
          <p>Last 28 days · ${`${count(night?.attributes.sample_count)} nights · ${count(morning?.attributes.sample_count)} mornings`}</p>
          ${!hasSamples ? html`<p>No graded observations yet.</p>` : nothing}
          <dl class="quality-metrics">${METRICS.map(([key, label, unit]) => {
            const entity = get(key); const value = finite(entity?.state);
            return html`<div><dt>${label}</dt><dd><bdi>${value === null ? "—"
              : `${value.toFixed(1)} ${unit}`}</bdi>
              <small>${`${count(entity?.attributes.sample_count)} samples`}</small></dd></div>`;
          })}</dl>
          ${recent ? html`<p>Last 7 days: ${`${count(recent.nights)} nights · ${count(recent.mornings)} mornings`}</p>
            <dl class="quality-metrics">${METRICS.map(([key, label, unit]) => {
              const value = finite(recent[key]);
              return html`<div><dt>${label}</dt><dd><bdi>${value === null ? "—"
                : `${value.toFixed(1)} ${unit}`}</bdi></dd></div>`;
            })}</dl>` : nothing}
          ${excludedCount !== null ? html`<p>${`${excludedCount.toFixed(0)} excluded · ${count(night?.attributes.pending)} pending`}</p>` : nothing}
        ` : nothing}
        ${providerEntries.map((p) => {
          const captured = hass.states[p.entity_id];
          const statistic = captured?.attributes.forecast_statistic_id;
          const verifiedEntry = entries.find((e) => e.device_id === entry.device_id
            && e.translation_key === "forecast_archive_verified_at"
            && hass.states[e.entity_id]?.attributes.forecast_statistic_id === statistic);
          return html`<p><bdi>${captured?.attributes.provider ?? ""}</bdi></p>
            <dl class="quality-times"><div><dt>Last capture</dt>
              <dd><bdi>${stamp(captured)}</bdi></dd></div><div><dt>Archive verified</dt>
              <dd><bdi>${stamp(verifiedEntry ? hass.states[verifiedEntry.entity_id] : undefined)}</bdi></dd></div></dl>`;
        })}
      </div>
    </details>`;
  })}`;
}

export const qualityStyles = css`
  .quality { border-block-start: 1px solid var(--divider-color); padding-block-start: 12px;
    color: var(--primary-text-color); font-size: .875rem; line-height: 1.6; }
  .quality summary { cursor: pointer; display: list-item; }
  .quality summary:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 4px; }
  .quality-status { color: var(--secondary-text-color); margin-inline-start: 8px; }
  .quality-status[data-state="error"], .quality-status[data-state="degraded"] { color: var(--error-color); }
  .quality-body p { margin: 10px 0; color: var(--secondary-text-color); }
  .quality-metrics, .quality-times { margin: 12px 0; display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 16px; }
  .quality dt { color: var(--secondary-text-color); }
  .quality dd { margin: 2px 0 0; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
  .quality dd small { display: block; color: var(--secondary-text-color); }
`;
