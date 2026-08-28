/**
 * Entry point. Registers every card and announces them to the picker.
 *
 * Presence in the card picker is distribution: a card with a name, a
 * description and a preview gets discovered by people browsing, which a hidden
 * `mode:` option on a single card never does.
 */

import { SolarSanityForecastCard } from "./forecast-card";
import { SolarSanityCard } from "./status-card";
// Imported for its registrations: two strategies and one dashboard entry.
import "./strategy";
import { VERSION } from "./types/hass";

export { SolarSanityCard, SolarSanityForecastCard };

window.customCards = window.customCards ?? [];
window.customCards.push(
  {
    type: "solar-sanity-card",
    name: "Solar Sanity",
    description: "Tells you whether your solar data adds up. Stays quiet when it does.",
    preview: true,
    documentationURL: "https://github.com/fadmaz/ha-solar-sanity",
  },
  {
    type: "solar-sanity-forecast-card",
    name: "Solar Sanity: tomorrow's forecast",
    description:
      "What your forecast provider said a day ahead, kept as it was issued rather than as it was revised.",
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
