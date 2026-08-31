"""Score each forecast provider against what the roof actually produced.

``analysis/forecast.py`` has been able to answer this since it was written. It
is 367 lines with thirty tests and, until now, no caller — the archive it needed
on the other side did not exist, and then it did and nothing joined them up.

This module is that join, and it is deliberately thin. Everything that decides
anything lives in ``analysis/forecast.py``, which is pure and testable without
Home Assistant; what is here is fetching, unit conversion and three refusals.

**The refusals are the point.** A forecast bias is a number a person will repeat
to their installer, so the ways it can be quietly wrong matter more than the
arithmetic:

*Units.* The archive is kWh — ``async_forecast_series`` asks the recorder for
``{"energy": "kWh"}`` — and the coordinator's buckets are Wh. A missed division
gives a bias of -99.9%, which ``_snap`` rounds to -100 and reports as a
fault-shaped number rather than as the nonsense it is.

*The unresolvable day.* ``local_day`` returns ``(None, False)`` when there is no
zone to resolve against, and ``forecast.build_days`` uses that return as a
dictionary key. Every hour of the window would land under ``None``, merging a
month into one enormous "day" whose ratio means nothing and which is long enough
to pass every eligibility test. It has to be refused, not passed through.

*A missing hour.* ``forecast.build_days`` already drops a day where any hour the
provider expected something from has no measurement beside it. That is its rule
and it is right — one absent midday hour is most of a day's energy. Nothing here
may fill such an hour with a zero to keep the day.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ._local_time import local_day
from .analysis.forecast import Bias, build_days, eligible, estimate
from .analysis.model import BucketSource, Role
from .const import CONF_FORECAST_ENTRIES
from .statistics_source import async_forecast_series, dayahead_statistic_id

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .coordinator import SolarSanityCoordinator

_LOGGER = logging.getLogger(__name__)

#: How far back to score. Long enough for a season to have moved on, short
#: enough that a provider which has since improved is not held to last winter.
SCORING_WINDOW_DAYS = 60

#: Watt-hours in a kilowatt-hour. Written down because getting it wrong is the
#: most damaging thing this module can do and the least visible.
WH_PER_KWH = 1000.0


@dataclass(frozen=True, slots=True)
class ProviderScore:
    """One provider's bias, and enough to say where it came from."""

    entry_id: str
    name: str
    bias: Bias


def _actual_kwh_by_hour(coordinator: SolarSanityCoordinator) -> dict[datetime, float]:
    """Generation this integration measured, by UTC hour, in kilowatt-hours.

    Only whole hours, and only hours with a reading. A partial hour is not a
    small hour — ``build_days`` in the residual engine drops anything not
    exactly 3600 s for the same reason, and a forecast compared against
    three-quarters of an hour under-reads by a quarter with nothing to show for
    it.

    Provenance is deliberately *not* filtered on. A statistics-derived hour is a
    weaker measurement of generation than our own, but it is a measurement, and
    scoring a provider on the days we happened to be running would bias the
    result toward whatever the weather did while this integration was up.
    """
    pv_keys = [spec.key for spec in coordinator.specs if spec.role is Role.PV]
    if not pv_keys:
        return {}

    actual: dict[datetime, float] = {}
    for bucket in coordinator.buckets:
        if bucket.seconds != 3600:
            continue
        values = [value for key in pv_keys if (value := bucket.value(key)) is not None]
        if len(values) != len(pv_keys):
            continue
        actual[bucket.start_utc] = sum(values) / WH_PER_KWH
    return actual


def _local_date_or_refuse(coordinator: SolarSanityCoordinator):
    """A ``local_date`` callable, or ``None`` when the zone cannot be resolved.

    Returning a function that yields ``None`` would be worse than returning
    nothing: ``forecast.build_days`` keys its days on this, so every hour in the
    window would collapse into a single entry that is sixty days long, passes
    every size test, and produces a confident figure about nothing.
    """
    zone = coordinator.time_zone
    if zone is None:
        return None

    probe, _ = local_day(dt_util.utcnow(), zone)
    if probe is None:
        return None

    def resolve(when: datetime) -> date:
        day, _dst = local_day(when, zone)
        if day is None:  # pragma: no cover - the probe above rules this out
            raise ValueError("unresolvable local day")
        return day

    return resolve


def _from_mean(coordinator: SolarSanityCoordinator) -> bool:
    """Whether any generation hour in the window came from an hourly mean.

    Passed through to ``estimate``, which widens what it will assert on. An
    hourly mean cannot say whether its hour was complete, and a forecast scored
    against an hour that was three-quarters watched is scored against a figure
    nobody can vouch for.
    """
    pv_keys = [spec.key for spec in coordinator.specs if spec.role is Role.PV]
    return any(
        bucket.source.get(key) is BucketSource.LTS_MEAN
        for bucket in coordinator.buckets
        for key in pv_keys
    )


async def async_score_providers(
    hass: HomeAssistant, coordinator: SolarSanityCoordinator
) -> list[ProviderScore]:
    """Every configured provider, scored against measured generation.

    Returns an empty list rather than raising: this runs on a timer beside the
    analysis, and a forecast that cannot be scored must not cost a house its
    verdict.
    """
    entry_ids = list(coordinator.entry.data.get(CONF_FORECAST_ENTRIES) or [])
    if not entry_ids:
        return []

    resolve = _local_date_or_refuse(coordinator)
    if resolve is None:
        _LOGGER.debug("no resolvable time zone; forecasts not scored")
        return []

    actual = _actual_kwh_by_hour(coordinator)
    if not actual:
        return []

    end = dt_util.utcnow()
    start = end - timedelta(days=SCORING_WINDOW_DAYS)
    from_mean = _from_mean(coordinator)

    scores: list[ProviderScore] = []
    for entry_id in entry_ids:
        provider = hass.config_entries.async_get_entry(entry_id)
        if provider is None:
            continue

        forecast = await async_forecast_series(hass, dayahead_statistic_id(entry_id), start, end)
        if not forecast:
            # ``None`` is a failed query and ``{}`` is an empty archive. Neither
            # is a provider that forecasts badly, and neither may be reported as
            # one.
            continue

        days = eligible(build_days(forecast, actual, resolve))
        scores.append(
            ProviderScore(
                entry_id=entry_id,
                name=provider.title or entry_id,
                bias=estimate(days, from_mean=from_mean),
            )
        )
    return scores
