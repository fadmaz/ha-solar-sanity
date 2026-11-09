/**
 * Entry point. Registers every card and announces them to the picker.
 *
 * Presence in the card picker is distribution: a card with a name, a
 * description and a preview gets discovered by people browsing, which a hidden
 * `mode:` option on a single card never does.
 */

// Imported for its registrations: the card editors. `getConfigElement` on
// each card creates one by tag name, which only works once the module has run.
import "./editors";
import { SolarSanityBatteryCard } from "./battery-card";
import { SolarSanityForecastCard } from "./forecast-card";
// Imported for its registrations: two strategies and one dashboard entry.
import "./strategy";
import { VERSION } from "./types/hass";

export { SolarSanityBatteryCard, SolarSanityForecastCard };

window.customCards = window.customCards ?? [];
window.customCards.push(
  {
    type: "solar-sanity-battery-card",
    name: "Solar Sanity: battery",
    description:
      "When your battery will be full, how low it will go tonight, and how much the house can use until the sun is back.",
    preview: true,
    documentationURL: "https://github.com/fadmaz/ha-solar-sanity",
  },
  {
    type: "solar-sanity-forecast-card",
    name: "Solar Sanity: tomorrow's forecast",
    description:
      "Tomorrow's solar forecast as your roof is expected to deliver it, corrected by what it has delivered before.",
    preview: true,
    documentationURL: "https://github.com/fadmaz/ha-solar-sanity",
  },
);

// eslint-disable-next-line no-console
console.info(
  `%c SOLAR-SANITY %c ${VERSION} `,
  "color:#fff;background:#0b6e63;font-weight:700",
  "color:#0b6e63;background:#fff",
);
