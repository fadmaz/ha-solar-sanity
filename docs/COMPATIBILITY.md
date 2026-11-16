# Installation compatibility

These checks describe Solar Sanity 0.53.0. Use **Setup** to see which
features have the measurements they need on your installation.

| Installation | Capability and evidence | Field validation |
| --- | --- | --- |
| Battery with zero export | Battery outlook and archive replay on the reference download. Zero is a supported export setting. | Battery outlook and archival were verified on 0.52.0. Live 0.53.0 verification is pending. |
| Solar only, no configured export limit | The native planning actions can use the hourly-validated factor of 1.00. Automated cases cover matching forecasts, shifted peaks and provider fallback. | Pending on an independent site with measured export. |
| Fixed limit with a production curtailment sensor | Automated cases cover zero export, an observed reduction, missing coverage and restoration across reload. | Pending on a site with a confirmed curtailment signal. |
| Unknown export, or fixed limit without a curtailment sensor | Existing corrections can still support manual planning. Hourly validation reports the missing export or curtailment evidence. | Depends on the available measurements. |

The integration test matrix covers Home Assistant 2025.11.0, 2026.2.3
and 2026.11.1. Automated scenarios use synthetic measurements.
