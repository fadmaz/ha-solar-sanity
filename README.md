# Solar Sanity

**Where your battery is heading tonight, learned from your own house.**

[![Validate](https://github.com/fadmaz/ha-solar-sanity/actions/workflows/validate.yml/badge.svg)](https://github.com/fadmaz/ha-solar-sanity/actions/workflows/validate.yml)
[![HACS](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz)
[![License](https://img.shields.io/github/license/fadmaz/ha-solar-sanity)](LICENSE)

> [!WARNING]
> Early development. The outlook has been graded against one real installation;
> the thresholds are still being checked against others.

---

## The problem

You have a battery and a dashboard that tells you where it is now. What you
actually want to know is where it will be:
- **Will it last the night?**
- **When will it be full?**
- **How much can the house use before the sun is back?**

The usual answer is a template sensor dividing the charge by the current load. It
jumps every time the kettle goes on, and it knows nothing about tomorrow's
weather or the evening you always have.

## What this does

Solar Sanity learns how your house uses energy, hour by hour, from its own
history. It projects the battery's charge from now until the forecast runs out,
and reads five answers off that one projection:

| Sensor | Answers |
| --- | --- |
| Battery full at | When will it reach its charging target? |
| Battery reaches reserve at | When will it reach the reserve, if before the sun takes over? Unknown means it lasts |
| Lowest battery before sunrise | How low will it fall tonight? |
| Safe average load tonight | How much can the house draw, on average, until the sun takes over? |
| Spare solar | How much sunshine will the battery have no room for? |

**Data:**
- Consumption comes from your history: the backfill reads up to a month of it
  at setup, so most houses have an answer on the first day.
- The night read-outs work without a forecast provider.
- *Full at* and *spare solar* need one. An inverter with nowhere to send a
  surplus records production cut back to what it could use, so history
  understates the sun.
- The two times are steady on a close call. When the battery is projected to
  miss full or reserve by a point or two, the time already shown stays, rather
  than jumping to tomorrow and back with every new charge reading. An earlier
  time always shows at once.

**Learned from your own meters,** and shown as attributes:
- **How far your forecast is off** (`forecast_scale`): what your panels delivered
  against what the forecast said a day ahead, over the last fortnight, counting
  only hours the battery had room in. On the installation it was built on, the
  forecast ran at twice the real output.
- **How far it is off on a day like today, and like tomorrow**
  (`forecast_scale_today`, `forecast_scale_tomorrow`): the same, learned mostly
  from the days whose forecast looked like that one. A forecast is rarely off by
  the same amount every day. Dirty panels, shading or a provider set up for
  the wrong roof show most in full sun. On the installation it was built on,
  the roof delivered about a third of a sunny day's forecast and nearly all of a
  cloudy one's. These are the factors the outlook projects with.
- **What the inverter draws for itself** (`inverter_standby_w`): power the house
  meter never sees, measured on the hours the battery carried the house alone in
  the dark.

**It never counts grid charging.** The question it answers is whether the
battery alone would get the house there, which is the one that matters during an
outage and the safe one otherwise. When the battery has been charged from the
grid recently, an attribute says so.

**It publishes unknown rather than a guess.** Every unknown state carries a
`reason` attribute saying why.

### How good is it?

Every night it can be graded against what the battery really did.
`scripts/backtest_outlook.py` replays a month of a diagnostics download. On the
installation it was built on (30 kWh, reserve 35%, Forecast.Solar), over 29 real
nights:
- the lowest-battery figure was off by 2.5 points on average, with a bias of +0.1;
- the call on whether the battery reaches its reserve before sunrise was right
  78 times out of 85 projections;
- the "will it be full today" call was wrong on 8 of 26 complete mornings.

Incomplete nights and mornings are excluded, with a reason in the replay.

## What it deliberately does not do

- **It does not report money.** Not savings, not payback, not bills. Those
  numbers depend on a tariff that changes, and they are unverifiable.
- **It does not control anything.** No switches, no battery scheduling, no
  automation of your hardware. It reads and it tells you.
- **It does not draw another power-flow diagram.** That is well served already.
  Everything here lives on the time axis.

---

## Issued predictions and collection health

Since 0.35.0, the integration keeps its first published prediction in the five
minutes after 08:00 and 20:00 in Home Assistant's time zone. Each record stays
unchanged. An outage or a missing outlook produces a missed checkpoint, not a
prediction reconstructed with the benefit of hindsight.

The night is graded after its recorded takeover time. The morning's fill call
is graded through 22:00 local, so a battery not yet full at 09:00 has not failed
a prediction for the afternoon. Incomplete observations and grid charging that
disturbs the result are excluded with reasons. An hourly maximum proves that
full occurred; minute-level timing is graded only when state history supplies it.

Four new sensors expose descriptive performance over the last 28 days:

| Translation key | Meaning |
| --- | --- |
| `night_lowest_mae` | Mean absolute lowest-charge error in percentage points (`pp`) |
| `reserve_call_accuracy` | Correct reserve calls, percent |
| `morning_fill_accuracy` | Correct morning fill calls, percent |
| `fill_time_mae` | Mean absolute fill-time error, minutes; only precisely observed crossings |

Each carries its sample count, pending/excluded counts and a seven-day summary.
No samples means unknown. These rolling figures are not cumulative counters and
do not make a claim about another installation.

`collection_status` reports starting, OK, degraded or error. Per provider,
`forecast_last_captured_at` is the integration's last usable capture and
`forecast_archive_verified_at` is its last read-back of matching archive data.
Verification is distinct from submitting a batch to the recorder. Its timestamp
does not claim to be the physical disk-write time.

The journal is local and private, retained for 90 days and at most 180 records
per installation. Diagnostics include it; inspect the download before sharing.
Battery settings, sources, time zone and model revision separate the scoring
contexts. Updating a display name does not change an entity's identity.

## Forecast history

Home Assistant throws away yesterday's solar forecast. Its forecast
integrations set no `state_class` on their energy sensors, so no long-term
statistics are recorded and the history is purged within about ten days. The
Energy Dashboard also *sums* multiple forecast providers into one line, so you
cannot compare them even while they are live.

Solar Sanity records each provider's forecast into Home Assistant's own
statistics engine, as two separate series:

| Series | What it holds |
| --- | --- |
| `solar_sanity:forecast_<provider>` | The latest revision of every hour. Right for display |
| `solar_sanity:dayahead_<provider>` | Each hour as first seen at twelve hours of lead, and never revised |

The split matters. A provider revises its forecast all day, so by the time an
hour has passed the rolling series holds a figure issued *minutes* before it,
which is a nowcast. Scoring that would flatter every provider equally and mean
nothing.

Capture starts the moment you install the integration. It is the one thing in
the product that cannot be backfilled later.

**Forecast.Solar on Home Assistant before 2026.11.** Forecast.Solar labels each
hour with the moment it ends. Home Assistant passed that label on unchanged until
2026.11, and Solar Sanity corrects it on older releases, so every hour is where
it belongs.

**Scoring ships, and it usually says nothing.**
- Each configured provider gets an accuracy sensor comparing what it said the
  day before against what your roof then produced. It is signed the way you
  would read it: negative means the system produced less than promised.
- A second sensor moves tomorrow's figure by that bias once there is a
  correction worth applying.
- A `rescore_forecasts` service answers on demand.

**The default answer is still no.** A figure needs:
- twenty-one comparable days across at least twenty-eight, spread across the
  window;
- stability under a split-half test;
- no correlation with the size of the day, and no drift.

Until then the sensor reads unknown and its `reason` says what it is waiting for.

---

## Cards

Both ship inside the integration and register themselves. One install, always
version-matched.

**Solar Sanity: battery** shows the battery outlook on one card:
- the charge now;
- one sentence worth leading with ("Full at 14:35, in 2 h 10 min", "Lasts until
  the sun, at 06:40", "Reaches the reserve at 04:10");
- the safe load, the lowest before sunrise and the spare sun;
- the projected charge over the next day and a half against your reserve and
  target, with the night shaded.

Times are in Home Assistant's time zone. It finds its sensors in the entity
registry, so renaming them does not lose them.

**Solar Sanity: tomorrow's forecast** shows tomorrow as your roof is expected
to make it: your provider's current forecast, curve and total, corrected by how
much of it your roof has delivered on days forecast like tomorrow. The figure
for an ideal roof is not shown; the provider's own sensors already show it.
Until the correction is learned, it shows the provider's forecast and says so.
It reads the forecast again every half hour. It is hand-rolled inline SVG, so
it re-themes with no JavaScript.

Neither takes any configuration. Drop one on a dashboard and it finds its own
data. Each has an editor for when there is a second installation to choose
between, or a single provider to isolate.

A dashboard still using the old status card (`custom:solar-sanity-card`) shows
the battery card in its place.

### Laying them out for you

**Settings → Dashboards → Add dashboard → Solar Sanity** builds one, already
arranged. Nothing to configure and no YAML.

For a view inside a dashboard you already have:

```yaml
views:
  - strategy:
      type: custom:solar-sanity
```

Either route lays out the battery first and the forecast under it. With more than
one installation it emits one battery card per house, each told which one it
belongs to, rather than letting a card choose silently between them.

## Installation

Via HACS as a custom repository (category: **Integration**), then restart and
add **Solar Sanity** from Settings → Devices & Services.

If your dashboards are in YAML mode you will need to add the card resource by
hand; the log says so explicitly rather than failing silently.

### Releases, and why there is no 1.0.0 yet

Every release is tagged and carries a written changelog. The version number is
deliberately still `0.x`, and the reason is specific rather than modest: **this
has been validated on one installation.** `1.0.0` will be cut when the outlook's
answers hold up on houses its author did not build it on.

Pre-releases are published on the same repository. To see them, open Solar
Sanity in HACS, use the three-dot menu and turn on **beta versions**; the setting
is per repository. If you run one, the most useful thing you can send back is
the diagnostics download.

Read it before you post it. Coordinates, API keys and passwords are stripped.
**Your entity ids are not**, deliberately: they are what makes a report
answerable. They do name your hardware, and the file also carries your hourly
consumption and charge level for the last month. If that matters to you, send it
privately rather than attaching it to a public issue.

## Setup

1. **Map your sensors.** They are pre-filled from your Energy Dashboard where
   possible. Consumption and generation are required: the outlook learns the house
   from what it uses.
   Power readings use W or kW. Energy readings must be cumulative totals with
   `state_class: total` or `total_increasing`; an unclassified energy reading
   has no known period and is excluded, with the reason in the log and diagnostics.
2. **Pick the battery's charge level, if you have one.** The wizard then asks how
   big the battery is:
   - **usable capacity;**
   - **reserve:** the level your inverter will not go below;
   - **charging target:** usually 100%.
3. **Pick any solar forecast integrations** you want recorded.

Everything chosen at setup can be changed afterwards through **Reconfigure**,
without losing any history. Battery settings are also under **Configure**,
with one more: an optional **battery power** sensor, for the battery card's
live line. Pick it when your battery channels are energy totals, which cannot
say what the battery is doing this second, and untick "positive while
charging" if your inverter signs it the other way.
Adding a second entry for the same house is caught rather than allowed to
quietly corrupt a shared forecast archive.

## Entities

| Entity | Meaning |
| --- | --- |
| `sensor.*_battery_full_at` | When the battery will reach its charging target. Needs a forecast provider |
| `sensor.*_battery_reaches_reserve_at` | When it will reach reserve, if before the sun takes over |
| `sensor.*_lowest_battery_before_sunrise` | Tonight's projected minimum. Its attributes carry the whole trajectory, the charge it started from, the reserve and target, the forecast scale and the inverter's standby |
| `sensor.*_safe_average_load_tonight` | The steadiest load the house can draw until the sun takes over |
| `sensor.*_spare_solar` | Sunshine the battery will have no room for. Needs a forecast provider |
| `sensor.*_usable_battery_energy` | What the battery can still give the house before its reserve, after the inverter's loss. A measurement, recorded as a statistic |
| `sensor.*_expected_use_tonight` | What the house and the inverter will use from dusk until the sun takes over, from the learned hours. Its `from` and `until` say when |
| `sensor.*_spare_solar_power_now` | Solar power going to waste now, because the battery is full or charging as fast as it can: what an air conditioner or a water heater could take at no cost to the battery or the grid. Worked every 30 seconds from the live readings; `potential_w` is what the sun could give, and `source` says whether that was measured or, while production is held down, the corrected forecast |
| `sensor.*_solar_power_now_corrected` | Your provider's forecast power now, as your roof would make it. Drawn between the hourly averages |
| `sensor.*_solar_energy_this_hour_corrected` | This hour's forecast, corrected |
| `sensor.*_solar_energy_next_hour_corrected` | The next sixty minutes, corrected |
| `sensor.*_solar_energy_remaining_today_corrected` | What is left of today, corrected |
| `sensor.*_solar_energy_today_corrected` | Today's whole forecast, corrected |
| `sensor.*_expected_tomorrow_corrected` | Tomorrow's forecast, corrected, with a `state_class` so it is recorded. Shown as "Solar energy tomorrow (corrected)" |
| `sensor.*_<provider>_accuracy` | One per configured provider: how close it has been, signed so negative means less than promised. Unknown until it is earned |

"Battery full at" and "Battery reaches reserve at" are timestamps: Home
Assistant shows them as "in 3 hours" by itself, and an Entities card with
`format: datetime` shows the exact date and time. A countdown sensor would be
the same answer rewritten every minute.

The first five corrected sensors exist when a forecast provider is configured. They
are the figures Forecast.Solar publishes, multiplied by what your roof has
delivered against that provider. With a battery, that is the outlook's learned
factor for a day forecast like today, or like tomorrow; without one, the
provider's stated accuracy. Each carries the `provider`, the `factor`, its
`basis` (`learned` or `scored`) and the `days` behind it, or a `reason` while
it is unknown. There is no corrected peak time: one factor for the whole day
cannot move the day's peak, so the provider's own peak-time sensors already
say it. The uncorrected "Forecast tomorrow" was removed in 0.33.0 for the same
reason.

**Download diagnostics** on the device page carries everything the outlook was
computed from: per-channel coverage, the hourly window, a month of charge level
and both forecast archives. It is enough to replay every night with
`scripts/backtest_outlook.py`.

## Development

```bash
pip install -r requirements-dev.txt   # Python 3.13; without it the Home Assistant tier is skipped
npm ci
python scripts/check.py               # every gate in the order CI needs them
```

That one command runs:
- ruff;
- the TypeScript typecheck;
- the Python suite;
- the card build and the card tests;
- the bundle size budget.

It stops at the first failure.

The outlook's arithmetic imports nothing from Home Assistant and is tested with
plain pytest. That is enforced structurally rather than by convention: an AST
check fails the build on any `homeassistant` import, on relative-parent imports,
on currency language, on `or 0` fallbacks, and on anything non-deterministic.
The currency check covers the cards and the translations too.

Before changing anything the outlook computes, replay a real download:

```bash
python scripts/backtest_outlook.py diagnostics/<download>.json --tz <your zone>
```

The bar is the one it passed: at least 14 distinct nights, the lowest-battery
figure within 5 points on average, and the reserve called right at least 85% of
the time. `frontend/dev/harness.html` renders the cards on synthetic data for
looking at them in a browser.

## Brand assets

The icon lives in `custom_components/solar_sanity/brand/` and is regenerated
with `python scripts/make_brand_assets.py`. Home Assistant serves brand images
from there as of 2026.3, taking priority over the CDN; the `home-assistant/brands`
repository no longer accepts custom-integration icons.

## License

[MIT](LICENSE)

### Accuracy and data quality

The product interface is English only, including both cards and their editors,
setup, entity labels and reasons. There is no card language selector.

The expandable **Accuracy and data quality** section reads the journal sensors;
it does not recompute scores in the browser. Unknown metrics mean there are no
complete observations yet. Collection faults and stale forecasts have separate
reasons. An unsupported energy classification also appears under Settings >
Repairs with the required source fix.

### Morning experiment

Solar Sanity can record a silent alternative daylight distribution alongside
issued forecasts. It does not change the active battery outlook. Diagnostics
show paired sample counts and every promotion gate. It needs at least 14 new
complete mornings and nights, no extra false fill promises, fewer missed fills
and no worse night performance before a reviewed activation. Missing training
or daylight coverage uses the existing shape. Historical replay is development
evidence only; it is not prospective validation.

The corrected tomorrow sensor's `corrected_hours` attribute carries the exact
curve drawn by the forecast card. `applied_factor` preserves the unrounded
multiplier; `factor` is the shorter descriptive figure.
