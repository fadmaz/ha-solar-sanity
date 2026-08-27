/**
 * The slice of Home Assistant this card actually touches.
 *
 * Vendored rather than taken from `custom-card-helpers`, which is effectively
 * unmaintained and ships a stale `HomeAssistant` type along with a websocket
 * dependency we do not need. Structural typing means this keeps working as
 * Home Assistant adds fields, and it is free at runtime.
 */

export interface HassEntity {
  entity_id: string;
  state: string;
  attributes: Record<string, unknown>;
  last_updated: string;
}

export interface HomeAssistant {
  states: Record<string, HassEntity>;
  config: { state: string };
  themes: { darkMode: boolean };
  language: string;
  translationMetadata: {
    translations: Record<string, { isRTL?: boolean }>;
  };
  callWS<T>(msg: Record<string, unknown>): Promise<T>;
  callService(
    domain: string,
    service: string,
    data?: Record<string, unknown>,
  ): Promise<unknown>;
  localize(key: string, ...args: unknown[]): string;
}

export interface LovelaceCardConfig {
  type: string;
  [key: string]: unknown;
}

export interface LovelaceCard extends HTMLElement {
  hass?: HomeAssistant;
  setConfig(config: LovelaceCardConfig): void;
  getCardSize?(): number | Promise<number>;
}

export interface LovelaceCardEditor extends HTMLElement {
  hass?: HomeAssistant;
  setConfig(config: LovelaceCardConfig): void;
}

export interface GridOptions {
  rows?: number;
  min_rows?: number;
  max_rows?: number;
  columns?: number | "full";
  min_columns?: number;
  max_columns?: number;
}

/** What the integration returns from `solar_sanity/info`. */
export interface SolarSanityInfo {
  version: string;
  api_version: number;
  instances: Array<{
    entry_id: string;
    title: string;
    providers: string[];
  }>;
}

/** The five honest outcomes. Never a percentage. */
export type SolarSanityStatus =
  | "ok"
  | "insufficient_data"
  | "not_checkable"
  | "investigating"
  | "fault_found";

export interface SolarSanityStatusAttributes {
  reason?: string | null;
  finding_code?: string | null;
  headline?: string | null;
  detail?: string | null;
  source_fix?: string | null;
  confidence?: string | null;
  channels?: string[];
  days_of_data?: number;
  deferred?: string[];
}

declare global {
  interface Window {
    customCards?: Array<{
      type: string;
      name: string;
      description?: string;
      preview?: boolean;
      documentationURL?: string;
    }>;
  }
}

declare const __SS_VERSION__: string;
export const VERSION: string =
  typeof __SS_VERSION__ === "string" ? __SS_VERSION__ : "0.0.0";
