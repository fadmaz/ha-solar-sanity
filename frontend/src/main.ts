/**
 * Entry point. Registers every card and announces them to the picker.
 *
 * Presence in the card picker is distribution: a card with a name, a
 * description and a preview gets discovered by people browsing, which a hidden
 * `mode:` option on a single card never does.
 */

import { SolarSanityCard } from "./status-card";
import { VERSION } from "./types/hass";

export { SolarSanityCard };

window.customCards = window.customCards ?? [];
window.customCards.push({
  type: "solar-sanity-card",
  name: "Solar Sanity",
  description: "Tells you whether your solar data adds up. Stays quiet when it does.",
  preview: true,
  documentationURL: "https://github.com/fadmaz/ha-solar-sanity",
});

// eslint-disable-next-line no-console
console.info(
  `%c SOLAR-SANITY %c ${VERSION} `,
  "color:#fff;background:#0b6e63;font-weight:700",
  "color:#0b6e63;background:#fff",
);
