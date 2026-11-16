# Solar Sanity reference

[User guides](../README.md#user-guides) · [Install and map your sensors](GETTING_STARTED.md)

## Settings

Open **Settings → Devices & services → Solar Sanity → Configure**. Each entry
has its own settings. Use **Reconfigure** to change measurement channels or
the list of linked forecast providers.

| Setting | Use |
| --- | --- |
| **Preferred forecast provider** | Choose a linked provider or **Auto**. Check **Setup → Forecast** for the provider actually used for each result. |
| **Battery charge level** | Battery state of charge as a percentage. |
| **Usable battery capacity (kWh)** | Energy the battery can store over its usable charge range. Required for the battery outlook. Range 0.1–1,000 kWh. |
| **Reserve (%)** | Set to the battery cutoff used by your system. Range 0–95%. Default 20%. |
| **Charging target (%)** | Set to the level at which your system stops charging. Range 10–100%. Default 100%. It must be above reserve. |
| **Battery power (optional)** | Signed power in W or kW for the live battery card and measured surplus check. Useful when the configured battery channels are cumulative energy totals. |
| **Battery power is positive while charging** | On by default. Turn it off when your signed power sensor is positive during discharge. |
| **Battery charge power limit** | The maximum power in W that reaches the battery. Enter a positive number or leave blank for automatic estimation. |
| **Inverter standby draw** | Additional inverter draw in W beyond measured house consumption. Enter 0 if the house meter already includes it. Leave blank for automatic estimation. |
| **Minimum planned battery charge** | Optional percentage from the configured reserve to 100%. Applies to manual load planning. Leave blank to use reserve. |
| **Grid-connected solar without a battery** | Opt in to estimated grid energy comparisons in **What if**. Available with no battery channels mapped. |
| **Maximum estimated additional grid energy** | Optional solar-only planning cap in kWh. Enter 0 to allow no additional draw, or leave blank to allow any estimated draw. |
| **Reserve warning lead time** | Lead time for **Reserve risk**, from 1 to 1,440 minutes. Default 120. |
| **Minimum measured surplus** | Threshold in W for **Solar surplus ready**. Default 1,500 W. |
| **Surplus hold time** | Time surplus must meet the threshold, from 1 to 60 minutes. Default 5. |
| **Alternate schedule days** | Optional experiment. Choose one to six days to form a second consumption group. An empty list uses one weekly schedule. |

**Setup** shows which value and source the battery projection uses for the
charge power limit and standby draw.

## Entities

Find the entities under the Solar Sanity device in **Settings → Devices &
services**. Home Assistant assigns entity IDs based on the installation and
entity names. Use the actual entity ID from your device in automations.

### Battery outlook

These entities need battery charge level and usable capacity.

| Entity name | What it reports |
| --- | --- |
| Battery full at | Time the charging target is projected to be reached, given a usable solar forecast. |
| Battery reaches reserve at | Time reserve is projected to be reached before solar covers the load. Check the reason if the time is unknown. |
| Lowest battery before sunrise | Projected low through the night window. Its attributes include the trajectory and assumptions. |
| Safe average load tonight | Estimated average load the battery could serve until solar takes over. |
| Spare solar | Solar energy the battery is projected to have no room for. Requires a forecast. |
| Usable battery energy | Energy the battery can still deliver to the house above reserve after conversion losses. |
| Expected use tonight | Estimated house and inverter energy use between dusk and solar takeover. The `from` and `until` attributes give the interval. |
| Spare solar power now | Estimated current solar power left after house demand and battery charging. The source identifies measured production or the corrected forecast. |
| Reserve risk | Battery reserve reached or expected within the configured warning time. |

**Battery full at** and **Battery reaches reserve at** are timestamp sensors.
To show an exact date and time in an Entities card, select `format: datetime`
for the row.

### Solar and collection

| Entity name | What it reports |
| --- | --- |
| Solar power now (corrected) | Forecast power now, estimated between hourly averages and corrected for your roof. |
| Solar energy this hour (corrected) | Corrected forecast for the current hour. |
| Solar energy next hour (corrected) | Corrected forecast for the next 60 minutes. |
| Solar energy remaining today (corrected) | Corrected forecast energy still to come today. |
| Solar energy today (corrected) | Corrected forecast total for today. |
| Solar energy tomorrow (corrected) | Corrected forecast total and hourly series used by the tomorrow card. |
| Solar surplus ready | Direct measurements show sustained surplus at or above the configured threshold. |
| Collection status | Starting, OK, degraded or error. Check `reason` for details. |
| Provider accuracy | Per-provider signed comparison between the forecast and your roof's production. |
| Provider forecast last captured | Per-provider timestamp for the last usable capture. |
| Provider archive last verified | Per-provider timestamp for the last successful Recorder read-back. |

An unknown forecast or an unavailable signal can have a `reason` attribute.
See [forecasts and history](FORECASTS_AND_HISTORY.md) for correction and
archive details.

### Accuracy and the experimental interval

| Entity name | What it reports |
| --- | --- |
| Night minimum mean absolute error | Average absolute error in night minimum charge, in percentage points. |
| Reserve prediction accuracy | Percentage of graded night forecasts that called reserve correctly. |
| Morning fill prediction accuracy | Percentage of graded morning forecasts that called charging target correctly. |
| Fill time mean absolute error | Average absolute fill time error in minutes, using precisely observed crossings. |
| Experimental 20:00 minimum lower bound | Lower limit for the dated evening forecast, when calibrated. |
| Experimental 20:00 minimum upper bound | Upper limit for the dated evening forecast, when calibrated. |

Accuracy measures are rolling summaries for the active setup, with counts
and exclusions. See [issued predictions](FORECASTS_AND_HISTORY.md#read-issued-predictions-in-history)
for how observations are graded.

## Actions

Find these actions by searching for **Solar Sanity** in Home Assistant's action
tools. Installation-specific actions take a Solar Sanity device as
`device_id`. In YAML use the actual device ID. Each action returns a response.
For a script or automation, use `response_variable` to read it in a later step.

| Action | Inputs and use |
| --- | --- |
| `solar_sanity.get_setup_check` | `device_id`. Read current measurement, forecast, battery, history and load planning readiness. |
| `solar_sanity.get_forecast_history` | `device_id` and optional `days` from 1 to 90, default 28. Read issued predictions and results. |
| `solar_sanity.simulate_load` | `device_id`, `power_w`, `duration_minutes` and optional `start_at`. Read a battery or solar-only comparison. |
| `solar_sanity.find_best_time` | `device_id`, `power_w`, `duration_minutes`, `finish_by` and optional `earliest_start`. Compare complete runs and get a recommendation when one qualifies. |
| `solar_sanity.preview_battery_settings` | `device_id`, current `projection_id` and proposed `battery_charge_power_limit_w` and `inverter_standby_w`. Use `null` to preview automatic estimation. |
| `solar_sanity.rescore_forecasts` | Recalculate forecast scores for configured providers from retained data. |
| `solar_sanity.schedule_load_reminder` | Save a reminder with the current `projection_id`, `power_w`, `duration_minutes` and `finish_by`. Set `destination` to a `notify` entity or a Companion App notification action. `earliest_start` and `script_entity_id` are optional. |
| `solar_sanity.get_load_reminder` | Read the active or most recent reminder. |
| `solar_sanity.cancel_load_reminder` | Cancel the active `reminder_id`. |
| `solar_sanity.check_load_reminder` | Check whether the current `reminder_id` can start now. |
| `solar_sanity.start_load_reminder` | Supply `reminder_id` and the current `projection_id` returned by the check. Requests the linked script after validating again. |

The two planning actions accept optional `minimum_soc_pct` for a battery or
`max_extra_grid_kwh` for solar only. Omit them to use the reserve or to plan
without a solar-only cap. Use timestamps with a UTC offset, such as
`2026-05-20T15:00:00+02:00`. The reminder actions require a Home Assistant
administrator.

For example, a script can ask what a 1,000 W load would do over the next hour.
Replace the placeholder with the Solar Sanity device ID.

```yaml
action: solar_sanity.simulate_load
data:
  device_id: YOUR_SOLAR_SANITY_DEVICE_ID
  power_w: 1000
  duration_minutes: 60
response_variable: comparison
```
