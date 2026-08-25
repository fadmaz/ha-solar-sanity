# Solar Sanity

**Tells you whether your solar data adds up — and stays quiet when it does.**

[![Validate](https://github.com/fadmaz/ha-smart-solar-manager/actions/workflows/validate.yml/badge.svg)](https://github.com/fadmaz/ha-smart-solar-manager/actions/workflows/validate.yml)
[![HACS](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz)
[![License](https://img.shields.io/github/license/fadmaz/ha-smart-solar-manager)](LICENSE)

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

> **Grid export is being counted as import.**
> The number is right; the sign is backwards. Over the last 6 days this
> accounts for 94% of the energy that does not add up.

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
| +0.02 … +0.08 | measured before the inverter — normal conversion loss, not a fault |
| +1 | the channel is counted twice |
| +2 | the sign is backwards |
| −1 | it sees half — one clamp on a two-conductor supply |
| −999 | kW reported as W |

Land at 1.43 and that is not a fault anyone can name, so nothing is said.
Possibly forever. That single rule is the main defence against false alarms.

**A perfect zero would be wrong.** Real systems lose energy — inverters are
95–97% efficient, batteries 85–95% round-trip — so expected loss is fitted per
installation over about three weeks and subtracted before any test runs. What
makes this workable is that the noise floor (4–5%) and the fault floor (50%+)
do not overlap.

**Five honest answers, not two:** `ok`, `insufficient_data`, `not_checkable`,
`investigating`, `fault_found`. Most systems are `insufficient_data` on day one
and plenty stay `investigating`. That is a real answer, not a failure.

**One finding at a time.** An uncorrected fault dominates everything, so a list
of five would mostly be echoes of the first.

---

## Forecast history

Home Assistant throws away yesterday's solar forecast. Its forecast
integrations set no `state_class` on their energy sensors, so no long-term
statistics are recorded and the history is purged within about ten days. The
Energy Dashboard also *sums* multiple forecast providers into one line, so you
cannot compare them even while they are live.

Solar Sanity records each provider's day-ahead forecast as it is issued, into
Home Assistant's own statistics engine. It starts the moment you install it —
this is the one thing in the product that cannot be backfilled later.

## Installation

Via HACS as a custom repository (category: **Integration**), then restart and
add **Solar Sanity** from Settings → Devices & Services.

The dashboard card ships inside the integration and registers itself. One
install, both halves, always version-matched. If your dashboards are in YAML
mode you will need to add the resource by hand; the log says so explicitly.

## Setup

Sensors are pre-filled from your Energy Dashboard where possible. Three
questions follow, and **"Not sure" is a real answer** — it defers to inference
rather than making you guess.

**Consumption is required.** Without it the arithmetic closes by definition:
`load` becomes whatever makes the equation balance, the residual is always
zero, and nothing is actually verified. Solar Sanity reports `not_checkable`
rather than a reassuring lie.

Mappings can be changed later without deleting the entry, so you keep your
history.

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
| `sensor.*_status` | One of the five outcomes. Never a percentage |
| `binary_sensor.*_data_problem` | On when something needs attention |
| `sensor.*_expected_tomorrow` | Tomorrow's forecast — with a `state_class`, so it is actually recorded |
| `sensor.*_data_completeness` | How much of the picture exists |
| `sensor.*_corrections_active` | Diagnostic overrides in effect |

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
npm ci && npm run build
```

The analysis engine imports nothing from Home Assistant and is tested with
plain pytest. That is enforced structurally rather than by convention — an AST
check fails the build on any `homeassistant` import, on relative-parent
imports, on currency language in user-facing copy, on `or 0` fallbacks, and on
anything non-deterministic.

The clean-house suite is a **gate, not a test**: thousands of healthy scenarios
across topologies, noise levels and seasons, every one asserting silence. It
must be green before any threshold anywhere is changed.

## License

[MIT](LICENSE)
