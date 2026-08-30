"""Rebuild an `AnalysisRequest` from a real installation's diagnostics download.

The engine is a pure function of its buckets, so a diagnostics file that carries
them is a reproducible test case. Without one, every question about a live
installation has to be settled by asking its owner to run something and wait a
day — which is how three separate diagnoses of the same house were made
confidently and retracted.

This is the other half of `coordinator.window_snapshot`. The synthetic house in
`house.py` remains the corpus; this is for the one or two real houses whose
behaviour the corpus turned out not to contain.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from analysis.model import (
    AnalysisRequest,
    Answer,
    Bucket,
    BucketSource,
    ChannelSpec,
    DeclaredTopology,
    Quality,
    Role,
)

#: The reverse of `coordinator._QUALITY_CODE` / `_SOURCE_CODE`. Deliberately not
#: imported from there: this module must stay importable with Home Assistant
#: absent, and reading the codes out of the file being replayed would let a
#: renamed code pass silently. A mismatch should fail here.
_QUALITY = {
    "O": Quality.OK,
    "M": Quality.MISSING,
    "R": Quality.RESET_SUSPECT,
    "D": Quality.DERIVED_FROM_MEAN,
    "S": Quality.STALE,
}

_SOURCE = {
    "i": BucketSource.OWN_INTEGRAL,
    "s": BucketSource.LTS_SUM,
    "m": BucketSource.LTS_MEAN,
}

_ROLES = {role.key: role for role in Role}

_ANSWERS = {"yes": Answer.YES, "no": Answer.NO, "unknown": Answer.UNKNOWN}


def payload_from(request: AnalysisRequest) -> dict[str, Any]:
    """The inverse of `request_from`, mirroring `coordinator.window_snapshot`.

    Here so the round trip can be exercised without Home Assistant. It is a
    second implementation of the same format, which is a real risk — so
    `tests/test_window_snapshot.py` asserts the two code tables are exact
    inverses of each other rather than trusting that they stay in step.
    """
    keys = [spec.key for spec in request.specs]
    inverse_quality = {v: k for k, v in _QUALITY.items()}
    inverse_source = {v: k for k, v in _SOURCE.items()}
    rows = [
        [
            bucket.start_utc.isoformat(),
            bucket.seconds,
            [bucket.wh.get(key) for key in keys],
            "".join(inverse_quality[bucket.quality[key]] for key in keys),
            "".join(inverse_source[bucket.source[key]] for key in keys),
        ]
        for bucket in request.buckets
    ]
    answers = {v: k for k, v in _ANSWERS.items()}
    declared = request.declared
    return {
        "data": {
            "window": {"keys": keys, "rows": rows},
            "entry": {
                "data": {
                    "channels": [
                        {"entity_id": s.entity_id, "role": s.role.key, "origin": s.origin}
                        for s in request.specs
                    ],
                    "has_battery": answers[declared.has_battery],
                    "grid_is_net": answers[declared.grid_is_single_net_sensor],
                    "load_whole_house": answers[declared.load_covers_whole_house],
                }
            },
            "coverage": {
                "utc_offset_hours": request.utc_offset_hours,
                "channels": [
                    {"key": s.key, "entity_id": s.entity_id, "unit": s.declared_unit}
                    for s in request.specs
                ],
            },
        }
    }


def load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def buckets_from(window: dict[str, Any]) -> tuple[Bucket, ...]:
    """The `window` block of a diagnostics file, as buckets.

    Raises rather than guessing on an unknown code. A file written by a version
    whose codes have moved is not a file this can replay, and silently mapping
    an unrecognised quality to OK would produce a confident wrong answer — the
    exact failure this module exists to prevent.
    """
    keys: list[str] = window["keys"]
    out: list[Bucket] = []
    for start, seconds, values, quality, source in window["rows"]:
        if len(quality) != len(keys) or len(source) != len(keys):
            raise ValueError(f"{start}: {len(keys)} channels, {len(quality)}/{len(source)} codes")
        out.append(
            Bucket(
                start_utc=datetime.fromisoformat(start),
                seconds=seconds,
                wh=dict(zip(keys, values, strict=True)),
                quality={k: _QUALITY[c] for k, c in zip(keys, quality, strict=True)},
                source={k: _SOURCE[c] for k, c in zip(keys, source, strict=True)},
            )
        )
    return tuple(out)


def specs_from(payload: dict[str, Any]) -> tuple[ChannelSpec, ...]:
    """Channel mapping as the entry actually holds it.

    `declared_unit` is not in the diagnostics file and nothing in the engine
    reads it — buckets are already watt-hours by the time they are written — so
    it is recorded as unknown rather than invented.
    """
    channels = payload["data"]["entry"]["data"]["channels"]
    coverage = {c["key"]: c for c in payload["data"]["coverage"]["channels"]}
    specs = []
    for channel in channels:
        role = _ROLES[channel["role"]]
        found = coverage.get(role.key, {})
        specs.append(
            ChannelSpec(
                key=role.key,
                role=role,
                entity_id=channel["entity_id"],
                friendly_name=found.get("entity_id", channel["entity_id"]),
                declared_unit=found.get("unit", ""),
                origin=channel.get("origin", "user"),
            )
        )
    return tuple(specs)


def declared_from(payload: dict[str, Any]) -> DeclaredTopology:
    data = payload["data"]["entry"]["data"]
    return DeclaredTopology(
        has_battery=_ANSWERS.get(data.get("has_battery"), Answer.UNKNOWN),
        grid_is_single_net_sensor=_ANSWERS.get(data.get("grid_is_net"), Answer.UNKNOWN),
        load_covers_whole_house=_ANSWERS.get(data.get("load_whole_house"), Answer.UNKNOWN),
    )


def request_from(payload: dict[str, Any], **overrides: Any) -> AnalysisRequest:
    """A complete request, exactly as the installation's own coordinator built it.

    `loss_model` is deliberately not carried across. The engine refits it from
    the buckets on every run, and passing the stored one in would replay a
    verdict that depended on a fit made from data no longer in the window.
    """
    window = payload["data"]["window"]
    if window is None:
        raise ValueError("this diagnostics file has no window block; it predates 0.16")

    buckets = buckets_from(window)
    if not buckets:
        raise ValueError("the window block is empty")

    fields: dict[str, Any] = {
        "now_utc": buckets[-1].start_utc,
        "specs": specs_from(payload),
        "buckets": buckets,
        "declared": declared_from(payload),
        "utc_offset_hours": payload["data"]["coverage"].get("utc_offset_hours", 0.0),
        "unrecorded_keys": (),
    }
    fields.update(overrides)
    return AnalysisRequest(**fields)
