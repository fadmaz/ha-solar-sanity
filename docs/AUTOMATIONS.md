# Set up reserve and surplus alerts

[User guides](../README.md#user-guides) · [Plan a load](LOAD_PLANNING.md)

Solar Sanity exposes two binary sensors for automations. Open **Solar Sanity:
tools → Setup → Automation signals** to see their current state, thresholds
and reasons. Set thresholds for each installation in **Settings → Devices &
services → Solar Sanity → Configure**.

## Reserve risk

Use **Reserve risk** to get a warning before your battery reaches the
configured reserve. It turns on when the battery is at or below reserve, or
a valid outlook expects it to get there within **Reserve warning lead time**.
The default is **120 minutes** and the range is 1–1,440 minutes. The signal
becomes unavailable when Solar Sanity lacks enough evidence to decide.

The reserve notification includes the configured reserve and the estimated
time of arrival, or tells you the battery has already reached it.

## Solar surplus ready

Use **Solar surplus ready** when an automation needs measured surplus that
has lasted for a set time. The defaults are **1,500 W** for **Minimum measured
surplus** and **5 minutes** for **Surplus hold time**. The hold time can be
1–60 minutes.

The signal needs live solar generation and separate, nonnegative grid import
and export sensors in W or kW. If you have a signed grid sensor that is
positive for import, you can derive the two sensors with templates. Use
`max(net, 0)` for import and `max(-net, 0)` for export, where `net` is the
signed reading. Preserve the source unit and propagate its unavailable state.
Check that both sensors keep reporting even when one direction stays at zero.
[Home Assistant template sensors](https://www.home-assistant.io/integrations/template/#state-based-template-sensors)

Battery installations also need live discharge power or a signed **Battery
power (optional)** sensor. Solar Sanity subtracts import and battery discharge
from measured export and caps the result at measured solar power. Import or
battery discharge above **50 W** stops the signal. Missing or stale readings
make it unavailable and restart the hold. Live power readings need a refresh
within three minutes. Cumulative energy totals can still support outlooks and
history.

## Import a notification blueprint

1. Open **Settings → Automations & scenes → Blueprints → Import blueprint**.
2. Paste the URL of the blueprint you want and import it.
   - [Solar Sanity — Reserve risk notification](https://github.com/fadmaz/ha-solar-sanity/blob/main/blueprints/automation/solar_sanity/reserve_risk_notification.yaml)
   - [Solar Sanity — Solar surplus notification](https://github.com/fadmaz/ha-solar-sanity/blob/main/blueprints/automation/solar_sanity/solar_surplus_notification.yaml)
3. Create an automation from the imported blueprint. Select the matching
   **Reserve risk** or **Solar surplus ready** entity for your installation.
4. Select one destination. Use **Notify entity** or enter a **Companion app
   notification action** such as `notify.mobile_app_my_phone`.
5. Save and enable the automation. It sends a notification when the selected
   condition becomes true. To check delivery separately, call the chosen
   notification action from Home Assistant's action tools.

[Home Assistant blueprint import instructions](https://www.home-assistant.io/docs/blueprint/tutorial/#share-informally)

### Optional estimated surplus notifications

A battery installation without an export meter can still receive an estimated
surplus notification from a usable corrected forecast. In the solar surplus
blueprint, turn on **Include estimated surplus** and select **Spare solar power
now** from the same installation.

The estimate uses the same power threshold and hold time. When measured and
estimated surplus are both ready, the notification uses the measured value.
Forecast-based messages are labeled **Estimated solar surplus**. They use
the notification path only. The **Solar surplus ready** sensor continues
to require direct measurements.
