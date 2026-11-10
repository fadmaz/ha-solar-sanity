"""Constants for Solar Sanity."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "solar_sanity"
PLATFORMS: Final = ["sensor"]

URL_BASE: Final = "/solar_sanity"
CARD_FILENAME: Final = "solar-sanity.js"

# --- configuration keys ---------------------------------------------------
CONF_CHANNELS: Final = "channels"
CONF_ROLE: Final = "role"
CONF_ENTITY_ID: Final = "entity_id"
CONF_ORIGIN: Final = "origin"

CONF_FORECAST_ENTRIES: Final = "forecast_entries"

#: Entity id of a battery state-of-charge sensor. Optional, and not a channel:
#: it is a level in percent rather than a flow in watt-hours. With the capacity
#: below, it is what the battery outlook projects from.
OPT_BATTERY_SOC: Final = "battery_soc_entity"
#: The battery's usable capacity, in kWh. With the charge-level sensor, the one
#: other thing the outlook cannot run without: a percentage only becomes hours
#: once it is a percentage of something.
OPT_BATTERY_CAPACITY_KWH: Final = "battery_capacity_kwh"
#: The level the battery is not taken below, in percent. "Runs out" in the
#: outlook means reaching this, not zero; most inverters cut off well above it.
OPT_BATTERY_RESERVE_PCT: Final = "battery_reserve_pct"
#: The level the battery is charged to, in percent. Usually 100; lower where the
#: inverter is set to stop early. Charging stops here in the projection and
#: anything beyond it counts as spare.
OPT_BATTERY_TARGET_PCT: Final = "battery_target_pct"
#: The battery's own power sensor, in W or kW, signed. Optional and shown only:
#: the battery card draws the flow from it. Nothing is computed from it, because
#: the projection already has the battery's energy from the mapped channels. It
#: exists because those are often lifetime energy counters, as on the reference
#: installation, and a counter cannot say what the battery is doing this second.
OPT_BATTERY_POWER: Final = "battery_power_entity"
#: Whether that sensor is positive while the battery charges. Inverters disagree;
#: absent means yes.
OPT_BATTERY_POWER_CHARGING_POSITIVE: Final = "battery_power_charging_positive"
#: Defaults for the two above. Twenty percent is a common lithium cut-off and
#: errs early for the ones set lower.
DEFAULT_RESERVE_PCT: Final = 20.0
DEFAULT_TARGET_PCT: Final = 100.0

# --- storage ---------------------------------------------------------------
STORAGE_VERSION: Final = 1
STORAGE_MINOR_VERSION: Final = 1
STORAGE_KEY_STATE: Final = f"{DOMAIN}.state"

# --- scheduling ------------------------------------------------------------
#: The battery outlook's quick tick. It recomputes only when the charge level
#: has moved since the last projection.
LIVE_INTERVAL: Final = timedelta(seconds=30)

#: Our own hourly integrator closes a bucket on the hour.
BUCKET_INTERVAL: Final = timedelta(minutes=5)

#: How much of an hour a power channel may be unreadable before the hour is
#: discarded rather than filled in. A momentary blip is not worth throwing an
#: hour away; three minutes of silence is a hole nobody can honestly fill, and
#: filling it with zero is the exact move this product exists to catch.
POWER_GAP_TOLERANCE_SECONDS: Final = 180.0

#: How stale a cumulative energy reading may be before the next one is treated
#: as a fresh baseline rather than as one interval's worth of energy.
#:
#: Differencing a counter says nothing about *when* the energy flowed, only that
#: it did. Two readings five minutes apart describe five minutes; two readings
#: two hours apart describe two hours, and crediting all of it to the hour the
#: sensor came back is how an inverter's morning reconnection becomes a fault
#: report. Generous, because a slow sensor is ordinary and a dropout is not.
ENERGY_MAX_AGE_SECONDS: Final = 15 * 60.0

#: How often the forecast providers are scored against the window. A score
#: rests on whole days, so nothing about it changes minute to minute.
SCORING_INTERVAL: Final = timedelta(hours=6)

# --- forecast capture ------------------------------------------------------
#: Home Assistant's own forecast integrations set no ``state_class`` on their
#: energy sensors, so their history lives only in the ``states`` table and is
#: purged after ~10 days. Capturing at issue time is the only way to score a
#: forecast later, and it is the one thing in this product that cannot be
#: backfilled.
FORECAST_CAPTURE_INTERVAL: Final = timedelta(minutes=30)
#: Shared by the projection and every corrected figure. An eight-hour-old
#: capture was still published by the corrected sensors in the 2026-11 audit.
FORECAST_MAX_AGE: Final = timedelta(hours=3)
FORECAST_STATISTIC_PREFIX: Final = f"{DOMAIN}:forecast_"

#: The day-ahead archive, kept separately from the rolling one above.
#:
#: A provider revises its forecast all day, and the rolling series keeps only
#: the latest revision for each hour — so by the time an hour has passed, what
#: is stored for it was issued minutes before, not the day before. Scoring that
#: and calling it a day-ahead forecast would flatter every provider equally and
#: mean nothing. An hour lands here on its first sighting at real lead time and
#: is never revised afterwards.
FORECAST_DAYAHEAD_PREFIX: Final = f"{DOMAIN}:dayahead_"

#: How far ahead an hour must still be to count as forecast rather than nowcast.
DAYAHEAD_MIN_LEAD_HOURS: Final = 12

#: How far back to look for a running total to resume from. Beyond this a gap is
#: treated as unresumable rather than restarted at zero.
FORECAST_SUM_LOOKBACK_DAYS: Final = 7

# --- services --------------------------------------------------------------
SERVICE_RESCORE_FORECASTS: Final = "rescore_forecasts"
