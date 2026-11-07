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

/**
 * One entity's registry entry as the frontend holds it: which integration made
 * it and under which translation key. Matching on these, rather than on entity
 * ids, survives a user renaming every entity.
 */
export interface HassEntityRegistryEntry {
  entity_id: string;
  platform?: string;
  translation_key?: string;
  device_id?: string;
}

export interface HomeAssistant {
  states: Record<string, HassEntity>;
  /** Optional here only so fixtures that predate it stay valid. */
  entities?: Record<string, HassEntityRegistryEntry>;
  config: { state: string; time_zone?: string };
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


declare global {
  interface Window {
    /** Dashboard strategies offered in the new-dashboard dialog. */
    customStrategies?: Array<{
      type: string;
      strategyType: "dashboard";
      name?: string;
      description?: string;
      documentationURL?: string;
    }>;
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
