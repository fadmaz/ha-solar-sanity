# Solar Sanity

**Tells you whether your solar data adds up — and stays quiet when it does.**

[![Validate](https://github.com/fadmaz/ha-solar-sanity/actions/workflows/validate.yml/badge.svg)](https://github.com/fadmaz/ha-solar-sanity/actions/workflows/validate.yml)
[![HACS](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz)
[![License](https://img.shields.io/github/license/fadmaz/ha-solar-sanity)](LICENSE)

> [!WARNING]
> Early development. The checks are conservative by design, but the thresholds
> are still being tuned against real installations.

---

## The problem

You have solar panels and a dashboard full of numbers. You have no way of
knowing whether those numbers are **correct**.

If a current clamp is on backwards, if a sensor reports kilowatts while claiming
watts, if your battery's charge and discharge are mapped to each other's slots —
nothing tells you. Your dashboard looks fine. It is confidently wrong, and every
figure downstream inherits the error.

## What this does

Over any hour, energy in must equal energy out:

```
solar + grid import + battery discharge  =  house load + grid export + battery charge
```

Solar Sanity checks that identity against your own sensors. When it does not
close, it names the sensor and explains what is wrong with it:

> **Grid export is counted the wrong way round**
> When Grid export reads a positive number, the energy is actually flowing the
> other way. The magnitude is right; the direction is backwards. Over the last
> 6 days this explains 94% of the energy that does not add up.

Findings appear in Home Assistant's **Repairs** panel, with the real fix
alongside — not buried in a log.

## What it deliberately does not do

- **It does not report money.** Not savings, not payback, not bills. Those
  numbers depend on a tariff that changes, and they are unverifiable.
- **It does not control anything.** No switches, no battery scheduling, no
  automation of your hardware. It reads and it tells you.
- **It does not draw another power-flow diagram.** That is well served already.
  Everything here lives on the time axis: days, weeks, seasons.

---

## Silence is the point

A diagnostic tool that cries wolf gets uninstalled in a week, so the design
budget is *fewer than one false finding per two hundred installations per year*.
Everything follows from that.

**It stays quiet unless it can name the fault.** Every fault reduces to
estimating one number per channel, and that number snaps to a small physical
set:

| Estimate | Means |
| --- | --- |
| ~0 | healthy |
| +0.02 … +0.10 | measured before the inverter — normal conversion loss, not a fault |
| +1 | the channel is counted twice |
| +2 | the sign is backwards |
| −1 | it sees half — one clamp on a two-conductor supply |
| −2 | it sees a third — one clamp on a three-phase supply |
| −999 | kW reported as W |

Land at 1.43 and that is not a fault anyone can name, so nothing is said.
Possibly forever. That single rule is the main defence against false alarms.

**A perfect zero would be wrong.** Real systems lose energy — inverters are
95–97% efficient, batteries 85–95% round-trip — so expected loss is fitted per
installation and subtracted before any test runs. The conversion term needs a
few days; the inverter's own idle draw is measured on hours with no generation
and needs about two hundred of them. What makes any of this workable is that
the noise floor (4–5%) and the fault floor (50%+) do not overlap.

**And it says what it measured.** A healthy verdict used to be the word `ok` and
nothing else, while the analysis behind it had found a continuous draw nothing
in the house accounts for. If there is one you are told — *"About 35 W flows
continuously that nothing measures"* — and the figures behind every verdict,
including `ok`, are in the diagnostics download.

The two conversion-loss notes are written and deliberately still silent. Their
wording says a few per cent of loss is normal and there is nothing to fix, and
the fit cannot yet tell that apart from a continuous draw you are paying for —
so on the present evidence they would sometimes be reassurance about a real
problem, and silence is the safer half of that trade.

**Five honest answers, not two:** `ok`, `insufficient_data`, `not_checkable`,
`investigating`, `fault_found`. Most systems are `insufficient_data` on day one
and plenty stay `investigating`. That is a real answer, not a failure.

**One finding at a time.** An uncorrected fault dominates everything, so a list
of five would mostly be echoes of the first.

**Naming a sensor takes a week.** The per-channel estimate is taken from the
largest hours only, so it needs about a hundred and sixty complete hours before
it means anything. Structural findings — an unmeasured battery, an unmeasured
export path — rest on shape rather than arithmetic and can be named from five
days.

---

## When your balance cannot close

Plenty of systems have no grid export sensor. Nothing measures what leaves the
house, so the identity is short a term and can never close — and the energy that
leaves looks exactly like generation that went missing.

Solar Sanity says so, and then checks what it still can. In the hours with no
generation nothing can be exported, so the arithmetic has to close, and that
half of the day is verified normally:

> Nothing measures what leaves your house, so only the 11 hours a day with no
> generation could be checked — in those, nothing can be exported and the
> arithmetic has to close. Your generation sensor is not covered by this,
> because it only produces energy during the hours that cannot be checked.

> About 4.9 kWh a day is unaccounted for while you have a surplus. With no
> export meter that is most likely what you are sending to the grid, but it
> cannot be told apart from a generation sensor reading high.

Those are **notes**: never a fault, never a Repairs entry, never an alarm. They
say what a verdict covers, because a clean status that quietly covered half the
hours would be worse than no status.

---

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
hour has passed the rolling series holds a figure issued *minutes* before it —
a nowcast. Scoring that would flatter every provider equally and mean nothing.

Capture starts the moment you install the integration. It is the one thing in
the product that cannot be backfilled later.

**Scoring is not shipped yet.** The engine that decides whether a bias figure has
been earned is written and tested, and its default answer is no: twenty-one
comparable days across at least twenty-eight, spread across the window, stable
under a split-half test, uncorrelated with the size of the day and not drifting.
Only then a magnitude gate — and every threshold widens by 1.6 when generation
comes from hourly means, so some installations will never qualify. That is the
correct outcome rather than a reason to lower a constant.

---

## Cards

Both ship inside the integration and register themselves. One install, both
halves, always version-matched.

**Solar Sanity** — the verdict, in three rows that never grow. A healthy day
says so in one line and shows nothing else; a fault shows the observation and a
button to the Repairs entry carrying the rest. Every degraded state is a
sentence rather than an empty box.

**Solar Sanity: tomorrow's forecast** — what your provider said a day ahead
about tomorrow, drawn from the immutable series above. Hand-rolled inline SVG,
so it re-themes with no JavaScript and uses Home Assistant's own solar colour.

Neither takes any configuration. Drop one on a dashboard and it finds its own
data — the status card picks up your installation, the forecast card draws every
provider you have.

Both have an editor for when the defaults are not what you want: a second
installation to choose between, or a single provider to isolate. The status
card's editor also answers a question the interface could not previously answer
at all, which is *which* installation the card picked when it picked for you.

### Laying them out for you

**Settings → Dashboards → Add dashboard → Solar Sanity** builds one, already
arranged. Nothing to configure and no YAML.

For a view inside a dashboard you already have:

```yaml
views:
  - strategy:
      type: custom:solar-sanity
```

Either route emits the verdict first and the forecast under it — the forecast is
what you look at when the verdict is boring. If you run more than one
installation it emits one status card per house, each told which one it belongs
to, rather than letting a card choose silently between them.

## Installation

Via HACS as a custom repository (category: **Integration**), then restart and
add **Solar Sanity** from Settings → Devices & Services.

If your dashboards are in YAML mode you will need to add the card resource by
hand; the log says so explicitly rather than failing silently.

### Releases, and why there is no 1.0.0 yet

Every release is tagged and carries a written changelog. The version number is
deliberately still `0.x`, and the reason is specific rather than modest: **this
has been validated on one installation.**

That one house has been genuinely useful — most of what shipped in the last
several releases was a defect it exposed, including two the test suite was
asserting the opposite of. But a fault detector calibrated against a single
roof is a fault detector with a sample size of one, and its thresholds have
never met a topology its author did not think of. `1.0.0` is a claim about that,
not about feature completeness, and it will be cut when the answers hold up
somewhere else.

Pre-releases are published on the same repository. To see them, open Solar
Sanity in HACS, use the three-dot menu and turn on **beta versions** — the
setting is per-repository, so it does not affect anything else you have
installed. If you run one, the most useful thing you can send back is
the diagnostics download.

Read it before you post it. Coordinates, API keys and passwords are stripped;
**your entity ids are not**, deliberately — they are what makes a mapping report
answerable, and a report without them usually cannot be acted on. They do name
your hardware, and the file also carries your hourly consumption for the last
month. If that matters to you, send it privately rather than attaching it to a
public issue.


## Setup

Sensors are pre-filled from your Energy Dashboard where possible. Three
questions follow, and **"Not sure" is a real answer** — it defers to inference
rather than making you guess.

**Consumption is required.** Without it the arithmetic closes by definition:
`load` becomes whatever makes the equation balance, the residual is always
zero, and nothing is actually verified. Solar Sanity reports `not_checkable`
rather than a reassuring lie.

Everything chosen at setup can be changed afterwards through **Reconfigure**,
including the topology answers and the forecast providers, without losing any
history. Adding a second entry for the same house is caught rather than allowed
to quietly corrupt a shared forecast archive.

## Corrections

When a fault is certain, Solar Sanity offers to adjust for it — framed as
*"applied so I can keep checking"*, never as *"fixed"*.

- Never applied without a click.
- Scoped to this integration only. Your sensor, your Energy Dashboard and your
  automations are untouched.
- Always shown alongside the real fix.
- Continuously re-tested, so if you repair the sensor properly you get told the
  correction is no longer needed.

They exist because one uncorrected fault masks every other one.

## Entities

| Entity | Meaning |
| --- | --- |
| `sensor.*_status` | One of the five outcomes. Never a percentage. Its attributes carry the reason, the finding, and any notes |
| `binary_sensor.*_data_problem` | On when something needs attention — including when the identity provably fails but no single sensor can be blamed |
| `sensor.*_expected_tomorrow` | Tomorrow's forecast — with a `state_class`, so it is actually recorded |
| `sensor.*_data_completeness` | How much of the picture exists right now |
| `sensor.*_corrections_active` | Diagnostic overrides in effect |
| `sensor.*_live_residual` | Instantaneous imbalance, in watts. Only created when every channel reports a rate |

**Download diagnostics** on the device page is the fastest way to understand a
verdict. It carries per-channel coverage, both forecast archives, and every
number that was measured and then not acted on — because "nothing could be
established" is not a useful answer without them.

When the numbers do not add up overnight it also carries a **night ledger**:
every channel's total over one agreed set of hours, with the residual those
lines add up to. So a shortfall becomes a subtraction you can check a line at a
time, and the size of each line says which channel is carrying it.
`night_ledger_hours` turns the gap into a rate: 35,100 Wh over 390 hours is 90 W
drawn continuously by something unmeasured. A channel you have not configured
gets no line rather than a zero, because reporting no export on a system with no
export meter states the one thing nobody can know without one.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q       # 887 tests
npm ci
npm test                        # 104 card tests
npm run build
```

The analysis engine imports nothing from Home Assistant and is tested with
plain pytest. That is enforced structurally rather than by convention — an AST
check fails the build on any `homeassistant` import, on relative-parent
imports, on currency language in user-facing copy, on `or 0` fallbacks, and on
anything non-deterministic. The currency check covers the cards and the
translations too, not only the Python.

The clean-house suite is a **gate, not a test**: 98 healthy scenarios across
topologies, noise levels and seasons, every one asserting silence. It must be
green before any threshold anywhere is changed.

## Brand assets

The icon lives in `custom_components/solar_sanity/brand/` and is regenerated
with `python scripts/make_brand_assets.py`. Home Assistant serves brand images
from there as of 2026.3, taking priority over the CDN — the `home-assistant/brands`
repository no longer accepts custom-integration icons.

## License

[MIT](LICENSE)
