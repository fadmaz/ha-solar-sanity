# Plan a load with What if

[User guides](../README.md#user-guides) · [Settings reference](REFERENCE.md#settings)

Use **Solar Sanity: tools → What if** to compare one extra load with the
current outlook. Look up the appliance's typical power and run duration.
For example, a 1,000 W load running for 60 minutes uses 1 kWh. Solar Sanity
shows how much of that run your installation could cover.

## Choose the planning mode

| Installation | Result | Optional limit |
| --- | --- | --- |
| Battery outlook configured | Baseline and extra-load charge curves, projected minimum charge, reserve and fill times, and energy the battery and solar could not cover. | **Minimum planned battery charge**, in percent. |
| Grid-connected solar without a battery | Estimated grid energy with and without the extra load during the proposed run, plus the difference. | **Maximum estimated additional grid energy**, in kWh. |

Enable **Grid-connected solar without a battery** under **Configure** to open
the solar-only planner. It needs completed house consumption history and a
fresh forecast from one provider covering the requested window. A roof
correction or a factor of 1.00 supported by the hourly check is required.
A grid import meter is optional. **Setup → Load planning** shows what is
missing if the planner cannot estimate a run.

To qualify for factor 1.00, set **Grid export** to match your installation.
Open **Setup → Solar forecast → Forecast evidence** to see how many days
qualified and whether the timing and error checks passed. The hourly check
starts when you configure the export mode. With a fixed limit, a production
curtailment sensor must confirm that each qualifying daylight hour ran
without reduction.
[How the forecast check works](FORECASTS_AND_HISTORY.md#forecast-evidence-for-planning)

Set your default limit under **Configure**. Inside **What if**, change or
clear it for the current comparison. An empty battery limit uses the reserve.
An empty grid limit allows any estimated additional grid energy. **0 kWh**
allows only runs with no estimated additional draw. The house can still draw
grid energy for its existing load during that interval.

## Reuse a saved load

Choose a **Saved load** to fill in its power and duration. Your start, finish
deadline and planning limit stay as entered. Select **Calculate** or **Find
best time** to run the new comparison. Editing the power or duration switches
the picker to **Manual** and keeps your draft values.

An administrator can choose **Save as load**, give the current power and
duration a name, and keep it in this installation's shared list. Use **Manage
saved loads** to edit or delete a load. Each installation holds up to 20
loads, and users with read access can select them. When a selected load
changes in the list, the open form keeps its draft and switches to **Manual**.
Scheduled reminders keep the power and duration used when they were created.

If the list fails to refresh while you edit a saved load, choose **Retry saved
loads** to return to your draft. If the list changed in the meantime, cancel
and reopen the editor to make your changes against the current list.

## Compare one start time

1. Enter **Extra power (W)** and **Duration (minutes)**. Duration can be any
   whole number from 1 to 1,440.
2. Set **Start** to **Now** or **Scheduled**. For a scheduled run, choose a
   date and time in Home Assistant's time zone.
3. Select **Calculate**. The comparison refreshes with a current outlook or
   when you edit the inputs while the tab is visible.

A scheduled run that has already begun is evaluated for its remaining
minutes. The full remaining run must fit in the current horizon. When a
local time occurs twice during a clock change, choose which occurrence to
use. For a clock change that skips the selected time, choose a valid minute.

On the battery curve, the solid line is the baseline and the dashed line
includes the extra load. The graph and table use the calculated times for
this comparison. The main battery card can keep an earlier estimate on a
close call to avoid jumping between times.

## Find a start before a deadline

1. Set **Start** to **Find best time**.
2. Enter **Finish by** and, optionally, **Earliest start**. Leave the
   earliest start empty to search from now.
3. Select **Find best time**. Compare **Recommended** with **Run now** when
   starting now is within the selected window. Solar-only plans with a
   configured export mode refresh automatically while this tab is open.
   Calculate again if the card marks another result outdated.

Solar Sanity checks starts on the quarter hour and at the exact ends of the
allowed start range. It ranks battery runs by least estimated additional
grid energy, then highest minimum charge, then earliest start. A battery
recommendation must stay above the reserve and meet any higher planning
minimum through the full outlook, including after the load finishes.

Solar-only runs are ranked by least estimated additional grid energy and
then earliest start, over a forecast window of up to 36 hours. If the limit
filters out every candidate, the card shows the lowest additional grid
estimate it found. For example, if the house would import 0.3 kWh by itself
and 0.8 kWh with the load, the extra draw is 0.5 kWh.

## Preview battery settings

Expand **Preview battery settings** in **What if** to compare current and
proposed values for **Battery charge power limit** and **Inverter standby
draw**. Both columns use the same starting charge, solar forecast and
consumption estimate. Review the calculated result, then save the settings
you want under **Configure**.

## Reminders and script starts

**Remind me** is for battery plans using the configured reserve. It requires a
Home Assistant administrator, a corrected Forecast.Solar outlook and a
confirmed provider update when checking or dispatching a start. Look at
**What if** or **Setup** to see whether the current forecast supports load
actions or is waiting for an update. Plans using an extra planning limit and
solar-only plans are for manual use.

1. Run **Find best time**. Below an eligible recommendation, choose a
   notification destination. You can select a `notify` entity or a Companion
   App action such as `notify.mobile_app_my_phone`.
2. If you want the option to start a script yourself from the card, select
   **Start script (optional)**. Choose a script that handles your appliance's
   readiness and run duration.
3. Select **Remind me**. Each installation holds one active reminder.
   **What if** shows the scheduled start, the deadline or why it is waiting.
   Use **Cancel reminder** to end it.
4. Select **Check now** to see whether starting at this moment still fits the
   plan. If the result is ready and you linked a script, select **Start with
   Home Assistant** to request it after a fresh check.

At the scheduled time, Solar Sanity checks the latest corrected forecast,
current charge, earliest start, deadline and reserve. It sends a notification
asking you to run the load manually when the full run fits and the battery
is projected to stay above reserve through the outlook. It can reschedule
within the saved window or wait for usable data. **Expired** means there is
no time left to finish the saved run.

A scheduled reminder sends a message. To run a linked script, use **Start
with Home Assistant** or the `solar_sanity.start_load_reminder` action.
**Start requested** means Home Assistant accepted the request and Solar Sanity
ended the reminder. The script handles the appliance itself. If
the result is unconfirmed, check the script trace or the appliance before
requesting another run. **Delivery unconfirmed** means the notification
attempt was interrupted and will not be retried.

[Home Assistant actions for planning and reminders](REFERENCE.md#actions)
