# Understand forecasts and history

[User guides](../README.md#user-guides) · [Entity reference](REFERENCE.md#entities)

## Read the battery and solar cards

The battery card projects charge from your most recent usable charge reading,
measured household history and expected solar production. It shows the
projected minimum, when charge could reach reserve or its target, and how
much the house could use on average before the sun takes over. The projection
assumes no future grid charging. Reserve means your configured cutoff.
Battery energy and solar estimates can change when the inputs change.

The night outlook can use measured production history when there is enough
history to project from. Projected charging target and spare solar need a
forecast. An inverter that reduces production once demand is covered can
make historical production understate available sunlight.

The tomorrow card draws the same corrected hourly series used by the
**Solar energy tomorrow (corrected)** sensor. It shows gaps where forecast
hours are missing and uses Home Assistant's time zone. The correction comes
from comparing the provider's forecast with production from your roof. With
a battery, it can use a learned correction for similar days. With solar only,
it needs a usable measured forecast bias. **Setup** shows when the correction
is still learning and why a result is unavailable.

Under **Configure → Preferred forecast provider**, choose the preferred
linked provider or leave it on **Auto**. A fallback can supply a result when
the preferred provider fails or lacks the requested horizon. Different
providers can supply the battery outlook, today's forecast and tomorrow's
forecast. Check **Setup → Forecast** to see the active sources. Forecast
captures have a three-hour freshness limit.

The **Changes** tab compares two valid outlooks. It shows the starting
charge, solar, expected consumption, correction factors and charging
assumptions that changed. Forecast and load energy use the future period
both outlooks cover. The comparison is available while both outlooks are
held in memory for the same setup.

## Use the forecast archives

Solar Sanity captures the providers you link and stores two hourly series
per provider in Recorder. Recording starts when you link the provider.

| Series | Use |
| --- | --- |
| `solar_sanity:forecast_<provider>` | The latest received revision of each hour, useful for display and comparing it with the current provider payload. |
| `solar_sanity:dayahead_<provider>` | The first recorded value for an hour seen at least 12 hours in advance. Use it to evaluate what the provider forecast ahead of time. |

A successful capture means Solar Sanity obtained usable forecast data.
**Last submitted batch** tells you when it queued values for Recorder.
**Last verified archive read-back** tells you when it read back matching
content. Open **Setup** to see which of the archive series are verified.

The provider's **accuracy** sensor is a signed estimate of how much more or
less your roof produced than forecast. Negative means production fell below
the forecast. A statement needs at least 21 comparable days spanning at
least 28 days, plus stability checks. The sensor explains what is missing
while it is unknown. Installations with batteries can learn a correction
from suitable days sooner.

## Forecast evidence for planning

Open **Setup → Solar forecast** to see the configured grid export mode,
curtailment status and **Forecast evidence** for each provider. A production
curtailment sensor can confirm actual reduction. A live reading near a
positive export limit suggests possible reduction. A zero export reading
leaves the available solar power undetermined.

The hourly check looks at up to 45 completed local days in the current
setup. It pairs the archived day-ahead forecast with complete daylight
measurements. Fixed-limit installations also need evidence that reduction
was inactive for every compared hour. Missing hours and incomplete evidence
exclude the day. At least 21 usable training days spanning 28 or more days
must support a stable factor of 1.00. On seven later days the total absolute
hourly error must stay within 20% of measured production. A one-hour shift
that improves the comparable error by more than five percentage points
indicates a timing problem.

Passing this check enables factor 1.00 as a fallback for manual solar-only
planning. **What if** shows the source and capture time. The solar forecast
sensors keep their existing correction rules. Changing the export or
production measurement setup starts a new evidence window.

## Read issued predictions in History

Solar Sanity saves the first valid battery outlook in the five minutes after
**08:00** and **20:00**, in Home Assistant's time zone. It keeps the original
prediction and settings context for up to **90 days**, with a maximum of
180 records per installation. An empty checkpoint is marked **Missed** when
the integration had no usable prediction for that window.

Open **History** to select **7**, **28** or **90 days**. Each record shows
the issued prediction, its result and an explanation when the observation
could not be graded. **Previous setup** marks older comparisons from another
settings context.

- **Morning:** the fill decision is graded through **22:00** local time.
  An hourly maximum can show the target was reached. Minute-level errors
  use the recorded time of an actual charge reading.
- **Evening:** the minimum charge and reserve decision are graded through
  the end of the saved night window. Incomplete measurements or external
  charging can exclude the result.
- **Solar coverage:** for eligible evening records, compare the forecast's
  handover time with the first measured hour in which solar generation
  covered house use plus estimated inverter standby draw. The comparison uses
  total energy for the full hour. If that hour starts after the forecast's
  handover time, the row can also show the lowest charge recorded between
  those times.

The battery card's **Accuracy and data quality** section summarizes the
most recent 28 days for the active context and includes a seven-day summary.
Read the sample counts alongside the errors. It can take several complete
nights and mornings before all the measures are available. For example,
three graded nights means the displayed minimum-charge error is based on
three nights.

## Experimental results

The **experimental 20:00 interval** is a dated range for the saved evening
forecast's lowest charge. It becomes available after at least 30 calibration
nights and 14 later validation nights meet the 80% coverage target. The
range expires at the end of that saved night.

The morning experiment compares an alternative distribution of solar energy
through the day. **Alternate schedule days** prepares a separate usage
experiment if your household follows different routines on some days.
Those experiments collect comparison evidence alongside the active
predictions. Leaving **Alternate schedule days** empty uses the same weekly
schedule for all days. [Project validation details](ARCHITECTURE.md)
