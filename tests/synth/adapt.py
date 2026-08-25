"""Turn a synthetic ``Series`` into an ``AnalysisRequest``.

Kept separate from the generator so the house stays a pure physics model and
this file owns everything about the engine's input contract.
"""

from __future__ import annotations

from datetime import timedelta

from analysis.model import (
    AnalysisRequest,
    Bucket,
    BucketSource,
    ChannelSpec,
    DeclaredTopology,
    LossModel,
    Quality,
    Role,
)

from tests.synth.house import Series

ROLES: dict[str, Role] = {
    "pv": Role.PV,
    "grid_import": Role.GRID_IMPORT,
    "grid_export": Role.GRID_EXPORT,
    "battery_charge": Role.BATTERY_CHARGE,
    "battery_discharge": Role.BATTERY_DISCHARGE,
    "load": Role.LOAD,
}

FRIENDLY: dict[str, str] = {
    "pv": "Solar production",
    "grid_import": "Grid import",
    "grid_export": "Grid export",
    "battery_charge": "Battery charging",
    "battery_discharge": "Battery discharging",
    "load": "House consumption",
}


def specs_for(
    channels: tuple[str, ...] = tuple(ROLES), *, origin: str = "user"
) -> tuple[ChannelSpec, ...]:
    return tuple(
        ChannelSpec(
            key=key,
            role=ROLES[key],
            entity_id=f"sensor.{key}",
            friendly_name=FRIENDLY[key],
            declared_unit="Wh",
            origin=origin,
        )
        for key in channels
    )


def to_request(
    series: Series,
    *,
    specs: tuple[ChannelSpec, ...] | None = None,
    declared: DeclaredTopology | None = None,
    loss_model: LossModel | None = None,
    source: BucketSource = BucketSource.OWN_INTEGRAL,
    missing: dict[str, set[int]] | None = None,
) -> AnalysisRequest:
    """Build a request. ``missing`` marks hours where a channel has no reading.

    Marked hours get ``Quality.MISSING`` and a ``None`` value — never a zero.
    That distinction is the whole point of the ``None``-hostility suite.
    """
    channel_specs = specs if specs is not None else specs_for()
    keys = tuple(s.key for s in channel_specs)
    absent = missing or {}

    buckets: list[Bucket] = []
    for hour in range(series.hours):
        wh: dict[str, float | None] = {}
        quality: dict[str, Quality] = {}
        sources: dict[str, BucketSource] = {}
        for key in keys:
            if hour in absent.get(key, set()):
                wh[key] = None
                quality[key] = Quality.MISSING
            else:
                wh[key] = series.data[key][hour]
                quality[key] = Quality.OK
            sources[key] = source
        buckets.append(
            Bucket(
                start_utc=series.start + timedelta(hours=hour),
                seconds=3600,
                wh=wh,
                quality=quality,
                source=sources,
            )
        )

    return AnalysisRequest(
        now_utc=series.start + timedelta(hours=series.hours),
        specs=channel_specs,
        buckets=tuple(buckets),
        declared=declared or DeclaredTopology(),
        loss_model=loss_model,
    )
