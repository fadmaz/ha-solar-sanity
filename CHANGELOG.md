# Changelog

All notable changes are documented here. This project follows Semantic Versioning.

## [0.2.0] - 2026-08-26

### Fixed

- **The statistics backfill produced nothing whenever a power sensor was
  mapped**, so "an answer on day one rather than day seven" was aspirational
  rather than true. It asked for `change`, which only exists for statistics
  with a sum; power sensors are `measurement` class and have none, so every row
  came back empty and one missing channel invalidated the whole hour.

  Power channels are now read as the hourly `mean` and multiplied by the hour.
  That is a better figure than our own sampling — the recorder observed every
  state change, where polling sees one in three hundred.

- **Mean-derived readings were discarded rather than graded.** `Bucket.value`
  treated anything that was not `OK` as absent, so even once the backfill
  returned data it would have been thrown away. They are now usable but weaker:
  the tolerance widens and no finding built on them can be called certain.
  `Quality.DERIVED_FROM_MEAN` and `BucketSource.LTS_MEAN` finally have a writer.

Closes #5.

## [0.1.6] - 2026-08-25

### Changed

- `Live residual` is no longer created on systems where it cannot report. It
  needs every balance channel to give a rate, so one energy channel rules it
  out — an amount cannot answer "what is flowing right now". Previously the
  entity existed and was permanently blank; being disabled by default hid that
  rather than fixing it.

## [0.1.5] - 2026-08-25

### Fixed

- **Data completeness stuck at 0%.** Sensors describing live state were only
  rewritten when the coordinator updated, which is every six hours. If the
  integration loaded before the inverter's entities had published — the normal
  case for MQTT-backed inverters — the first reading found nothing readable and
  nothing corrected it for six hours. The 5-minute sampling tick now refreshes
  those entities, without re-running the analysis.

## [0.1.4] - 2026-08-25

### Fixed

- **Three sensors could never produce a value.** `Expected tomorrow` and
  `Live residual` had value functions returning `None` unconditionally, so they
  advertised capabilities that did not exist. `Data completeness` reported days
  of history under a name and description promising the fraction of inputs
  present — a different question wearing the same unit.

  All three now measure what they claim: tomorrow's total comes from the
  captured forecast payload, the live residual from the most recent snapshot,
  and completeness from how many configured channels are currently readable.

  `Live residual` remains blank on any system with an energy channel, because
  the live tier is skipped there. That absence is the honest answer; a zero
  would not be.

## [0.1.3] - 2026-08-25

### Fixed

- **Energy sensors were integrated as if they were power.** A daily-resetting
  total deposited roughly the whole running day into every hour — wrong by
  ~11.5x per day on generation, and up to ~1900x on a lifetime battery counter.
  The engine's response was not silence: it would name a high-confidence
  "counted twice" fault with a one-click button that zeroed the channel. Power
  is now integrated, energy differenced, and a reset marks the hour untrusted
  rather than guessing.
- **Unit conversion could stop every channel dead.** Home Assistant's
  converters raise `HomeAssistantError`, which the handler did not catch, so
  one odd sensor produced a traceback every five minutes and no channel
  accumulated at all.
- **Discovery could suggest a forecast entity as the panels** — the
  predecessor's bug, reproduced.
- **"charge" matched "discharge"**, which could swap the two battery
  directions without ever surfacing as a fault.
- **The same entity could be mapped to two roles**, cancelling itself out of
  the balance.
- **Two "fix it" buttons did nothing** and the finding returned forever.
- **Days were UTC days**, splitting the solar curve in two away from Greenwich.
- A partial first bucket after a restart claimed to be a full hour.

### Changed

- Setup no longer asks whether you have a battery when you have just mapped
  battery sensors. Each question appears only when genuinely open.
- The forecast picker names the integration ("Forecast.Solar — Home")
  rather than showing a bare entry title.

## [0.1.2] - 2026-08-25

### Fixed

- **Setup failed with "Unknown error occurred".** The topology step built
  `selector.selector({"config_entry": {"multiple": True}})`, but
  `ConfigEntrySelector` has no `multiple` option — its config schema accepts
  `integration` and nothing else — so constructing that schema raised
  `vol.Invalid`. Home Assistant reported it on the *previous* step, which
  pointed at the wrong place entirely. Nobody could complete setup.

### Changed

- Forecast providers are now chosen from a named list rather than by config
  entry, so the picker shows "Forecast.Solar" instead of a UUID. The field is
  omitted entirely when no provider is installed.

### Added

- Tests that construct every config-flow and options-flow schema, and that
  import every module the way Home Assistant loads it. A selector's config is
  only validated when the schema is built, so nothing in the existing suite
  could have caught this.

## [0.1.1] - 2026-08-25

### Added

- Brand icons, shipped inside the integration at
  `custom_components/solar_sanity/brand/`. Home Assistant serves them from there
  as of 2026.3, taking priority over the brands CDN.

### Fixed

- The analysis package used absolute `from analysis.x import` internally, which
  resolved under pytest but failed on a real install with
  `No module named 'analysis'` — Home Assistant puts the *config* directory on
  the path, not the integration's own. The integration would not have loaded.
  Relative imports resolve either way.
- The release workflow could not attach assets; the default token is read-only
  on this repository and needed `contents: write`.

### Notes

- v0.1.0's archive predates the icons, so installs pinned to that tag have none.
- The `home-assistant/brands` repository stopped accepting custom-integration
  icons in 2026.3, so the HACS brands check now passes on locally-provided
  assets rather than being skipped.

## [0.1.0] - 2026-08-25

Complete rewrite. Replaces `ha_smart_solar_manager`, which was a threshold-based
optimizer; this is a data-verification tool and shares no code with it.

### Added

- **Energy balance validation.** Checks that solar + import + discharge equals
  load + export + charge, and names the responsible sensor when it does not.
  Findings surface in the Repairs panel with the real fix alongside.
- **Forecast retention.** Records each provider's day-ahead forecast as external
  statistics, because Home Assistant's forecast integrations set no
  `state_class` and their history is purged within about ten days.
- **A bundled dashboard card**, served and registered by the integration itself.
- **Discovery** from the Energy Dashboard, returning ranked candidates with
  reasons rather than a bare entity id.
- **Reconfigure flow**, so a mis-mapped sensor can be corrected without deleting
  the entry and orphaning its history.
- **Diagnostics** download, with location redacted.
- CI: hassfest, HACS validation, tests, lint, bundle-size budget, and explicit
  gates for the Home Assistant 2026.11 statistics and 2026.12 config-entry
  deprecations.

### Notes

- Nothing reports currency, and an AST check in CI keeps it that way.
- The analysis engine imports nothing from Home Assistant; purity is enforced
  structurally rather than by convention.
