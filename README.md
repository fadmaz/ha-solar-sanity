# Solar Sanity

Solar Sanity is a Home Assistant integration for solar forecasts, battery
outlooks and planning when to run a load. It learns your household's hourly
consumption and uses the solar your roof has actually delivered to correct the
provider's forecast.

[![Validate](https://github.com/fadmaz/ha-solar-sanity/actions/workflows/validate.yml/badge.svg)](https://github.com/fadmaz/ha-solar-sanity/actions/workflows/validate.yml)
[![HACS custom repository](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://www.hacs.xyz/docs/faq/custom_repositories/)
[![Latest release](https://img.shields.io/github/v/release/fadmaz/ha-solar-sanity)](https://github.com/fadmaz/ha-solar-sanity/releases/latest)

| Your installation | What you can use |
| --- | --- |
| Solar with a battery | See when the battery may reach reserve or its charging target. Compare an extra load against the battery outlook and set an optional planning minimum. |
| Grid-connected solar without a battery | Enable **What if** to compare estimated grid energy for an extra load now or later. Set an optional limit on how much grid energy the load can add. |
| Either setup | Check measurement and forecast readiness, see your corrected solar forecast, and keep an hourly archive for each linked provider. Live export measurements support the **Solar surplus ready** signal. |

## Get started

Requires **Home Assistant 2025.11.0 or later** and sensors for solar generation
and whole-home consumption. A battery outlook also needs battery charge level
and usable capacity. To use corrected forecasts, set up a provider such as
Forecast.Solar in Home Assistant first.

1. Add `https://github.com/fadmaz/ha-solar-sanity` as a custom integration
   repository. Use **Settings → Marketplace → Add from link** on Home Assistant
   2026.11 or later, or **HACS → Custom repositories** if you use HACS. Install
   the stable release and restart Home Assistant.
2. Go to **Settings → Devices & services → Add integration → Solar Sanity**.
   Map your sensors, enter battery settings if applicable, and select your
   forecast providers.
3. Go to **Settings → Dashboards → Add dashboard → Solar Sanity**. Open the
   tools card's **Setup** tab to see what is ready and what still needs data.
   You can also add any of the three Solar Sanity cards to an existing dashboard.

[Installation, sensor mapping and first-run checks](https://github.com/fadmaz/ha-solar-sanity/blob/main/docs/GETTING_STARTED.md)

## User guides

| I want to… | Read |
| --- | --- |
| Install, update, map sensors or fix missing data | [Get started](https://github.com/fadmaz/ha-solar-sanity/blob/main/docs/GETTING_STARTED.md) |
| Compare an extra load, find a time, or set a reminder | [Plan a load](https://github.com/fadmaz/ha-solar-sanity/blob/main/docs/LOAD_PLANNING.md) |
| Get a reserve or surplus notification | [Set up alerts](https://github.com/fadmaz/ha-solar-sanity/blob/main/docs/AUTOMATIONS.md) |
| Understand the charts, past predictions and accuracy | [Forecasts and history](https://github.com/fadmaz/ha-solar-sanity/blob/main/docs/FORECASTS_AND_HISTORY.md) |
| Find a setting, entity name or Home Assistant action | [Reference](https://github.com/fadmaz/ha-solar-sanity/blob/main/docs/REFERENCE.md) |

Battery outlooks account for the measured starting charge and assume no future
grid charging. The journal shows how predictions compared with real results and
why some readings could not be graded. The model has been checked against one
real installation. Use the results as estimates for your own setup.

Load planning and alert blueprints support manual decisions. If you link a
Home Assistant script to a reminder, **Start with Home Assistant** requests it
after you select the button and the current conditions pass a fresh check.

## Support and project information

- Check the guide for learning, stale forecasts and unavailable readings.
  [Troubleshooting](https://github.com/fadmaz/ha-solar-sanity/blob/main/docs/GETTING_STARTED.md#troubleshooting)
- For a reproducible problem, include your Home Assistant and Solar Sanity
  versions and the relevant reason from **Setup**. Keep full diagnostics
  private because they can contain equipment names, entity IDs and energy
  history. [Report a problem](https://github.com/fadmaz/ha-solar-sanity/issues/new/choose)

[Release notes](https://github.com/fadmaz/ha-solar-sanity/blob/main/CHANGELOG.md) ·
[Installation compatibility](https://github.com/fadmaz/ha-solar-sanity/blob/main/docs/COMPATIBILITY.md) ·
[Architecture and development](https://github.com/fadmaz/ha-solar-sanity/blob/main/docs/ARCHITECTURE.md) ·
[MIT license](https://github.com/fadmaz/ha-solar-sanity/blob/main/LICENSE)
