"""A year's production against what the installer promised.

The one question every owner has and no dashboard answers: was the number on the
quote true? Home Assistant knows what the array produced and the quote is a
figure the owner can type in, and nothing joins them.

**A note, never a finding.** Falling short of a guarantee is a conversation with
an installer, not a fault in the data — and this integration's whole promise is
that when it accuses something, it is right. A shortfall has a dozen honest
causes it cannot distinguish between: a bad year for weather, shading that grew,
a panel derating exactly as its warranty says it will. So it reports the figure
and stays out of the argument.

**Never annualised from a partial year.** A quote is an annual number and only a
year answers it. Scaling nine months up by four thirds compares three summer
months to an average one and flatters every installation checked in autumn — or
damns one checked in spring, which is worse, because that is somebody
telephoning an installer about a number this made up.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .analysis.model import Role
from .const import CONF_GUARANTEED_ANNUAL_KWH
from .statistics_source import async_energy_between

_LOGGER = logging.getLogger(__name__)

#: A year, and not a day less. See the module docstring.
YEAR_DAYS = 365


@dataclass(frozen=True, slots=True)
class YieldAgainstPromise:
    """What a year produced, against what was promised."""

    produced_kwh: float
    promised_kwh: float

    @property
    def share(self) -> float:
        return self.produced_kwh / self.promised_kwh

    @property
    def note(self) -> str:
        percent = self.share * 100.0
        if percent >= 100.0:
            return (
                f"Over the last year your array produced {self.produced_kwh:,.0f} kWh "
                f"against the {self.promised_kwh:,.0f} kWh it was sold on - "
                f"{percent:.0f}% of the promise."
            )
        return (
            f"Over the last year your array produced {self.produced_kwh:,.0f} kWh "
            f"against the {self.promised_kwh:,.0f} kWh it was sold on, which is "
            f"{percent:.0f}%. That is a figure to raise with whoever installed "
            f"it rather than a fault in your data - a short year has honest "
            f"causes this cannot tell apart, from the weather to a panel "
            f"derating exactly as its warranty allows."
        )


async def async_yield_against_promise(
    hass: HomeAssistant, coordinator
) -> YieldAgainstPromise | None:
    """The last 365 days against the configured guarantee, or ``None``.

    ``None`` for every reason there is: no guarantee configured, no generation
    channel, no recorder, an archive too short to be a year. Each of those is a
    question that cannot be answered rather than an answer of nought, and the
    difference matters here more than usual — the wrong one is a number somebody
    telephones an installer about.
    """
    promised = coordinator.entry.data.get(CONF_GUARANTEED_ANNUAL_KWH)
    if not isinstance(promised, (int, float)) or promised <= 0:
        return None

    pv_specs = [spec for spec in coordinator.specs if spec.role is Role.PV]
    if not pv_specs:
        return None

    end = dt_util.utcnow()
    start = end - timedelta(days=YEAR_DAYS)

    produced = 0.0
    for spec in pv_specs:
        # Summed across arrays rather than taking the first. A house with an
        # east and a west roof was sold one number for both of them.
        energy = await async_energy_between(hass, spec.entity_id, start, end)
        if energy is None:
            return None
        produced += energy

    if not await _async_archive_reaches_back(hass, pv_specs[0].entity_id, start):
        return None

    return YieldAgainstPromise(produced_kwh=produced, promised_kwh=float(promised))


async def _async_archive_reaches_back(hass: HomeAssistant, statistic_id: str, start) -> bool:
    """Whether the recorder actually holds the beginning of the window.

    A recorder purged to ninety days answers a 365-day query without complaint
    and returns ninety days of energy. Without this, that reads as a two-thirds
    shortfall against the guarantee on a roof with nothing wrong with it - the
    single most damaging thing this module could say.

    Asked as "did this array produce anything in the oldest month of the
    window". A purged archive answers nothing or nought; a real one answers with
    energy, because a working array produces in every month there is. That is a
    weaker test than counting rows and it is the one that needs no assumptions
    about the recorder's internals, which is worth more here than precision.
    """
    oldest = await async_energy_between(hass, statistic_id, start, start + timedelta(days=30))
    return oldest is not None and oldest > 0.0
