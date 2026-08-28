/**
 * Laying the cards out, so nobody has to.
 *
 * Two strategies over one function. A **view** strategy is the composable
 * piece — it fills a view someone has already made. A **dashboard** strategy is
 * the discoverable one: it appears by name in Home Assistant's new-dashboard
 * dialog, which is the only route here that costs a user no YAML at all.
 *
 * Neither generates a fixed list. The status card is emitted once per
 * installation and told which one it belongs to, because a card that picks
 * silently between two houses is showing a verdict about a house the reader may
 * not be looking at.
 *
 * When nothing is installed the strategy still emits the status card, and that
 * is deliberate: the card already renders "Solar Sanity is not set up yet" with
 * a button that goes and sets it up. Substituting a markdown card here would
 * mean writing that sentence a second time, in a second place, and watching the
 * two drift.
 */

import { isStatusEntity } from "./status-card";
import type { HomeAssistant, LovelaceCardConfig } from "./types/hass";

const ENTITY_PREFIX = "sensor.";

/** Every status entity this integration owns, in a stable order. */
export function installations(hass: HomeAssistant | undefined): string[] {
  if (!hass?.states) return [];
  return Object.keys(hass.states)
    .filter((id) => id.startsWith(ENTITY_PREFIX) && isStatusEntity(hass.states[id]))
    .sort();
}

/**
 * The cards for one view, in reading order.
 *
 * Verdict first. The forecast is the thing you look at when the verdict is
 * boring, and on a healthy system the verdict is one line — so putting it
 * second costs nothing and putting it first would bury the answer.
 */
export function buildCards(hass: HomeAssistant | undefined): LovelaceCardConfig[] {
  const found = installations(hass);

  const status: LovelaceCardConfig[] =
    found.length > 1
      ? found.map((entity) => ({ type: "custom:solar-sanity-card", entity }))
      : [{ type: "custom:solar-sanity-card" }];

  return [...status, { type: "custom:solar-sanity-forecast-card" }];
}

/** Fills a view. Selected with `strategy: {type: "custom:solar-sanity"}`. */
class SolarSanityViewStrategy extends HTMLElement {
  static async generate(
    _config: Record<string, unknown>,
    hass: HomeAssistant,
  ): Promise<Record<string, unknown>> {
    return { cards: buildCards(hass) };
  }
}

/** Makes a whole dashboard, and is what the new-dashboard dialog offers. */
class SolarSanityDashboardStrategy extends HTMLElement {
  static async generate(
    _config: Record<string, unknown>,
    _hass: HomeAssistant,
  ): Promise<Record<string, unknown>> {
    return {
      title: "Solar Sanity",
      views: [
        {
          title: "Solar Sanity",
          path: "solar-sanity",
          // Delegated rather than duplicated: the dashboard is one view, and
          // the view already knows how to fill itself.
          strategy: { type: "custom:solar-sanity" },
        },
      ],
    };
  }
}

customElements.define("ll-strategy-view-solar-sanity", SolarSanityViewStrategy);
customElements.define("ll-strategy-dashboard-solar-sanity", SolarSanityDashboardStrategy);

window.customStrategies = window.customStrategies ?? [];
window.customStrategies.push({
  type: "solar-sanity",
  strategyType: "dashboard",
  name: "Solar Sanity",
  description: "Whether your solar data adds up, and what was forecast a day ahead.",
  documentationURL: "https://github.com/fadmaz/ha-solar-sanity",
});

export { SolarSanityDashboardStrategy, SolarSanityViewStrategy };
