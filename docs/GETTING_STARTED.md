# Get started with Solar Sanity

[User guides](../README.md#user-guides) · [Settings reference](REFERENCE.md#settings)

## Before you install

- Run Home Assistant **2025.11.0 or later**.
- Have a Home Assistant sensor for **Solar generation** and one for **House
  consumption**. Solar Sanity learns how much your whole home uses at each hour.
- For a battery outlook, have a sensor that reports battery charge level in
  percent and know the battery's usable capacity in kWh.
- For corrected solar forecasts, configure a solar forecast integration such
  as Forecast.Solar first. Solar Sanity reads forecasts from providers that
  publish to Home Assistant's Energy integration.
- Keep Recorder running. Existing history helps Solar Sanity get started, and
  Recorder holds the forecast archive and the observations used for grading.

## Install or update

Use the Marketplace, HACS or manual installation, then add the integration.

### Marketplace (Home Assistant 2026.11 or later)

1. Go to **Settings → Marketplace → Add from link**.
2. Enter `https://github.com/fadmaz/ha-solar-sanity` under **GitHub link** and
   select **Add**. Connect GitHub if prompted. If asked for a type, choose
   **Integration**.
3. Open **Solar Sanity** and install the latest stable release.

[Home Assistant Marketplace custom repositories](https://www.home-assistant.io/integrations/marketplace/#custom-repositories)

### HACS

1. Open **HACS**, open the menu in the upper right and select **Custom
   repositories**.
2. Enter `https://github.com/fadmaz/ha-solar-sanity` and select **Integration**.
3. Open **Solar Sanity** in HACS and download the latest stable version.

[HACS custom repositories](https://www.hacs.xyz/docs/faq/custom_repositories/)

### Manual installation

Download `solar_sanity.zip` and extract its contents into
`<config>/custom_components/solar_sanity/`, with `manifest.json` directly
inside that directory. The ZIP includes the dashboard cards.
[Latest stable release](https://github.com/fadmaz/ha-solar-sanity/releases/latest)

### Add Solar Sanity

Restart Home Assistant, then go to **Settings → Devices & services → Add
integration** and select **Solar Sanity**.

For updates, use the entry in the Marketplace or HACS, or download the new
release ZIP for a manual installation. Restart Home Assistant and refresh the
dashboard. Solar Sanity registers the matching cards for dashboards that
store resources in Home Assistant.

## Map the measurements

Review any suggestions from your Energy Dashboard before saving them.

| Setup field | Which sensor to pick |
| --- | --- |
| **Solar generation** | Total output of the panels or inverter for this installation. Required. |
| **House consumption** | Whole-home consumption for the same installation. Required. Grid import alone misses loads served by solar or the battery. |
| **Grid import** | Energy or power drawn from the grid. For energy accounting, you can map one signed grid power sensor here if it goes negative when exporting and you leave **Grid export** blank. |
| **Grid export** | Energy or power sent to the grid, when measured separately. |
| **Battery charging** and **Battery discharging** | Measurements of the energy flowing into and out of the battery, when available. |
| **Battery charge level (optional)** | Battery state of charge in percent. Choose it to enable the battery outlook. |

Flow measurements can be in W or kW, or cumulative energy totals. An energy
sensor must report `state_class: total` or `total_increasing` so Solar Sanity
can calculate the energy added between readings. **Solar surplus ready** needs
separate live import and export power sensors.
[Measured surplus requirements](AUTOMATIONS.md#solar-surplus-ready)

On the next screen, choose the forecast providers to archive. If you selected
a battery charge sensor, enter **Usable battery capacity (kWh)**, **Reserve (%)**
and **Charging target (%)**. Use the settings of your inverter or battery.
The target must be above reserve. Defaults are 20% reserve and 100% target.

To change sensors or add and remove forecast providers later, open the menu on
the integration entry and choose **Reconfigure**. Use **Configure** to adjust
battery settings, preferred provider, load planning and alert options. Both are
under **Settings → Devices & services → Solar Sanity**. Each entry represents
one installation. Reconfigure an existing entry to change how that home is
measured.

## Open a dashboard

Go to **Settings → Dashboards → Add dashboard** and select **Solar Sanity** for
a ready-made layout. To add the cards individually, edit an existing dashboard,
choose **Add card**, then search for **Solar Sanity**.

| Card | What it shows |
| --- | --- |
| **Solar Sanity: battery** | Current and projected charge, reserve and target times, expected overnight use and available load headroom. |
| **Solar Sanity: tomorrow's forecast** | Tomorrow's corrected solar curve and energy total. Hours unavailable from the provider leave gaps. |
| **Solar Sanity: tools** | **Setup** checks, load planning in **What if**, saved predictions in **History**, and input comparisons in **Changes**. Tabs depend on your setup. |

Select the intended installation in the battery or tools card editor if you
have several. The forecast card editor can select a provider. For the tools
card, choose the **Default tab** if you want it to open somewhere other than
**History** for a battery or **Setup** for solar only.

Existing dashboards using `custom:solar-sanity-card` show the battery card.
Add **Solar Sanity: tools** separately to get the setup checks and planner.

### YAML dashboards

When dashboard resources are configured in YAML, add the Solar Sanity module
to the existing `lovelace.resources` list in `configuration.yaml`:

```yaml
lovelace:
  resource_mode: yaml
  resources:
    - url: /solar_sanity/solar-sanity.js
      type: module
```

Before Home Assistant 2026.2, use `mode: yaml` in place of `resource_mode: yaml`.
Add the resource once. Increase its version query string after an upgrade if
your browser keeps loading an older card. Dashboard storage mode handles the
resource version automatically.

To fill a view with the standard Solar Sanity cards, add a view with this
strategy to the dashboard configuration:

```yaml
views:
  - title: Solar Sanity
    path: solar-sanity
    strategy:
      type: custom:solar-sanity
```

[Home Assistant dashboard and resource settings](https://www.home-assistant.io/dashboards/dashboards/)

## Check the first run

Open **Solar Sanity: tools → Setup**.

1. Under **Measurements**, check that the live sensors read correctly and that
   Solar Sanity has completed hours. It can backfill up to about a month where
   Home Assistant kept usable statistics. With no suitable history, allow about
   three days of measurements for the first battery outlook. Missing hours can
   extend that time.
2. Under **Forecast**, check which provider supplies each outlook and the last
   capture and archive read-back. The roof correction needs past observations,
   so it can take longer than the first successful capture to show a value.
3. Under **Battery** and **Load planning**, check the selected mode and any
   reason shown. For a grid-connected installation with no battery, enable
   **Grid-connected solar without a battery** in **Configure** to use **What if**.
4. Set up [alerts](AUTOMATIONS.md) or [try planning a load](LOAD_PLANNING.md)
   once the data those features need is ready.

## Troubleshooting

| What you see | What to check |
| --- | --- |
| No card in the picker, or an old card after updating | Restart Home Assistant after installing the integration. Refresh the dashboard and check that the Solar Sanity resource is registered as a JavaScript module. In YAML mode, check the manual resource entry above. |
| A measurement is unavailable or rejected | Check the source sensor and its unit. For cumulative energy, check its state class. An unsupported classification can also appear in **Settings → System → Repairs**. |
| Battery outlook is unavailable | Verify that charge level is a percentage and updating, usable capacity is set, and there are enough completed consumption hours in **Setup**. |
| Corrected forecast is unknown | Check whether the provider covers the relevant period and refreshed recently. **Setup** distinguishes a missing or stale capture from a correction that is still learning. |
| A forecast was captured but the archive is pending | Check the last submitted batch and read-back in **Setup**. Recorder may still be committing the rows. Solar Sanity retries incomplete verification on its regular capture cycle. |
| History is empty or a checkpoint is pending | Issued predictions are collected from installation onwards at 08:00 and 20:00. Pending records need a complete observation window and Recorder data before grading. |
| An alert or reminder is unavailable | Check the [requirements for that alert](AUTOMATIONS.md) or the [load actions status](LOAD_PLANNING.md#reminders-and-script-starts). |

For deeper investigation, use **Download diagnostics** from the integration's
device or entry page. Keep the full download private. It may contain the names
of your equipment, entity IDs, battery readings and detailed energy history.
