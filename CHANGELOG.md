# Changelog

All notable changes are documented here. This project follows Semantic Versioning.

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
