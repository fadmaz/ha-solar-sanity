/** Response types and wall-clock parsing; battery arithmetic stays on the server. */
export interface SimulationProjection {
  lowest_pct: number | null; full_at: string | null; reserve_at: string | null;
  uncovered_energy_kwh: number; trajectory: {at: string; soc: number}[];
}
interface ProjectionBasis {
  projection_id: string; computed_at: string; horizon_end: string; time_zone: string;
  model_revision: string; forecast_used: boolean; reserve_pct: number; target_pct: number;
}

export interface SimulationResponse {
  planning_mode?: "battery";
  basis: ProjectionBasis;
  applied_start: string; applied_end: string; additional_energy_kwh: number;
  baseline: SimulationProjection; scenario: SimulationProjection;
  minimum_soc_pct?: number | null;
  meets_limit?: boolean | null;
}

export type LoadPlanning =
  | {mode: "battery"; reserve_pct: number; minimum_soc_pct: number | null}
  | {mode: "solar_only"; max_extra_grid_kwh: number | null; revision: string; recheck_forecast?: boolean}
  | {mode: null};

interface SolarBasis {
  projection_id: string; computed_at: string; horizon_end: string; time_zone: string;
  provider: string; forecast_captured_at: string;
  forecast_factor?: number; forecast_factor_source?: string;
}

export interface SolarGridTiming {
  start_at: string; end_at: string;
  baseline_grid_kwh: number; scenario_grid_kwh: number; additional_grid_kwh: number;
  meets_limit: boolean;
}

export interface SolarSimulationResponse {
  planning_mode: "solar_only";
  basis: SolarBasis;
  grid: SolarGridTiming;
  max_extra_grid_kwh: number | null;
}

export interface LoadTiming {
  start_at: string;
  end_at: string;
  minimum_soc_pct: number;
  additional_grid_kwh: number;
  reserve_at: string | null;
}

export interface LoadPlanResponse {
  planning_mode?: "battery";
  basis: ProjectionBasis;
  solar_corrected?: boolean;
  load_actions?: LoadActionAvailability;
  recommendation: LoadTiming | null;
  run_now: LoadTiming | null;
  highest_minimum_soc_pct?: number | null;
  minimum_soc_pct?: number | null;
  reason_code: "window_too_short" | "beyond_horizon" | "baseline_reaches_reserve" |
    "all_times_reach_reserve" | "baseline_below_planning_minimum" | "planning_minimum_not_met" | null;
}

export interface SolarPlanResponse {
  planning_mode: "solar_only";
  basis: SolarBasis;
  recommendation: SolarGridTiming | null;
  run_now: SolarGridTiming | null;
  lowest_extra_grid_kwh: number | null;
  max_extra_grid_kwh: number | null;
  reason_code: "grid_limit_not_met" | "window_too_short" | "beyond_horizon" | null;
}

export interface LoadActionAvailability {
  status: "ready" | "waiting_for_update" | "unsupported" | "unavailable";
  reason_code: string | null;
  reason: string | null;
}

export interface LoadPlanRequest {
  device_id: string;
  power_w: number;
  duration_minutes: number;
  finish_by: string;
  earliest_start?: string;
}

/** All instants matching a local minute: zero in a gap, two in a repeated hour. */
export function localInstants(local: string, timeZone: string): string[] {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(local)) return [];
  const naive = Date.parse(`${local}:00Z`);
  if (!Number.isFinite(naive) || new Date(naive).toISOString().slice(0,16) !== local) return [];
  const formatter = new Intl.DateTimeFormat("en-GB", {timeZone, year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hourCycle:"h23"});
  const wall = (instant: number) => {
    const p = Object.fromEntries(formatter.formatToParts(new Date(instant)).map(v => [v.type,v.value]));
    return Date.UTC(Number(p.year),Number(p.month)-1,Number(p.day),Number(p.hour),Number(p.minute));
  };
  const offsets = new Set([-36,-24,-12,0,12,24,36].map(h => {const probe=naive+h*3600000;return wall(probe)-probe;}));
  return [...new Set([...offsets].map(offset=>naive-offset).filter(t=>wall(t)===naive))].sort((a,b)=>a-b).map(t=>new Date(t).toISOString());
}
