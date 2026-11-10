/**
 * Laying the cards out, so nobody has to.
 *
 * Two strategies over one function. A **view** strategy is the composable
 * piece — it fills a view someone has already made. A **dashboard** strategy is
 * the discoverable one: it appears by name in Home Assistant's new-dashboard
 * dialog, which is the only route here that costs a user no YAML at all.
 *
 * Neither generates a fixed list. The battery card is emitted once per
 * installation with a battery outlook and pinned to it, because a card that
 * picks silently between two houses is showing a battery the reader may not be
 * looking at.
 *
 * When there is no outlook to show, the strategy still emits one battery card,
 * and that is deliberate: the card already says whether Solar Sanity is not set
 * up or the outlook is off, and how to turn it on. Substituting a markdown card
 * here would mean writing that sentence a second time and watching the two
 * drift.
 */

import { findInstallations, hasOutlook } from "./battery-model";
import type { HomeAssistant, LovelaceCardConfig } from "./types/hass";

/**
 * The cards for one view, in reading order: the battery, then the forecast,
 * which is what you look at when the battery has nothing to say.
 */
export function buildCards(hass: HomeAssistant | undefined): LovelaceCardConfig[] {
  const batteries = findInstallations(hass).filter(hasOutlook);
  const battery: LovelaceCardConfig[] =
    batteries.length > 1
      ? batteries.map((installation) => ({
          type: "custom:solar-sanity-battery-card",
          device_id: installation.deviceId,
        }))
      : [{ type: "custom:solar-sanity-battery-card" }];

  return [...battery, { type: "custom:solar-sanity-forecast-card" }, ...batteries.map(i => ({type: "custom:solar-sanity-tools-card", device_id: i.deviceId}))];
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
  description: "Where your battery is heading, and what was forecast a day ahead.",
  documentationURL: "https://github.com/fadmaz/ha-solar-sanity",
});

export { SolarSanityDashboardStrategy, SolarSanityViewStrategy };
