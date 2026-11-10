"""Produce one private, comparable evaluation report per installation and revision.

Usage: python scripts/evaluate_installation.py diagnostics/house.json
       --installation reference --tz Asia/Jerusalem
       --period-end-before 2026-11-08T00:00:00+00:00
       --output diagnostics/reference-report.json

Historical replay is development evidence. Only actual immutable issued records
can enter the prospective promotion and interval-validation gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "custom_components/solar_sanity"), str(ROOT / "scripts")]

import backtest_outlook as replay  # noqa: E402
from analysis.calibration import INTERVAL_REVISION  # noqa: E402
from analysis.daylight_shape import SHAPE_REVISION, daylight_thirds, learn_shape  # noqa: E402
from analysis.experiment import shape_gate  # noqa: E402
from analysis.grading import grade, summarize  # noqa: E402
from analysis.outlook import BatterySettings, build_steps, project  # noqa: E402
from analysis.profile import hourly_profile  # noqa: E402

from tests.synth import diagnostics  # noqa: E402

BASELINE = "baseline-0.34.2"


def unwrap(data: dict[str, Any]) -> dict[str, Any]:
    while "window" not in data:
        if isinstance(data.get("data"), dict):
            data = data["data"]
        elif isinstance(data.get("diagnostics"), dict):
            data = data["diagnostics"]
        else:
            raise ValueError("download has no measurement window")
    return data


def _battery(data: Mapping[str, Any]) -> BatterySettings:
    options = data.get("entry", {}).get("options", {})
    capacity = float(options["battery_capacity_kwh"])
    reserve = float(options["battery_reserve_pct"])
    target = float(options["battery_target_pct"])
    if (
        not all(math.isfinite(v) for v in (capacity, reserve, target))
        or capacity <= 0
        or not 0 <= reserve < target <= 100
    ):
        raise ValueError("invalid battery settings")
    return BatterySettings(capacity * 1000, reserve, target)


def _timed_full(soc, start, end, target):
    if soc.hourly_extremes:
        return None
    return next(
        (
            t
            for t, v in zip(soc.times, soc.values, strict=True)
            if start <= t <= end and v >= target - 1
        ),
        None,
    )


def _historical(data, buckets, soc, forecast, *, battery, zone, context, candidate=False):
    """Use the shipping projection functions and the same observation grader as HA."""
    windows = {
        date.fromisoformat(day): tuple(datetime.fromisoformat(t) for t in values)
        for day, values in data.get("daylight_windows", {}).items()
    }
    first = buckets[0].start_utc.astimezone(zone).date()
    last = buckets[-1].start_utc.astimezone(zone).date()
    local_hour = replay.local_hour_of(zone)
    rows = []
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        for hour, kind in ((8, "morning"), (20, "evening")):
            now = datetime.combine(day, time(hour), tzinfo=zone).astimezone(UTC)
            record = {
                "id": f"{day}:{kind}",
                "context": context,
                "checkpoint": kind,
                "issued_at": now.isoformat(),
                "model_revision": SHAPE_REVISION if candidate else BASELINE,
                "evidence_kind": "historical_replay",
                "result": {"status": "excluded", "reason_code": "not_ready"},
            }
            rows.append(record)
            initial = soc.at(now)
            load = hourly_profile(buckets, "load", local_hour, until=now)
            if initial is None or not load.ready:
                continue
            day_of = lambda t: t.astimezone(zone).date()  # noqa: E731
            if candidate:
                shape = learn_shape(
                    buckets,
                    forecast,
                    soc_max=soc.maxima,
                    full_pct=battery.target_pct,
                    until=now,
                    day_of=day_of,
                    daylight=windows,
                )
                target_day = day if kind == "morning" else day + timedelta(days=1)
                points = {t: v for t, v in forecast.items() if day_of(t) == target_day}
                if shape is None or daylight_thirds(points, windows.get(target_day)) is None:
                    record["result"]["reason_code"] = "shape_unavailable"
                    continue
            corrected = replay.scaled(buckets, forecast, soc, battery, now, zone=zone)
            steps = build_steps(
                now, load=load.wh, local_hour=local_hour, pv_forecast=corrected, pv_profile=None
            )
            if not steps:
                continue
            settings = replay.learned(buckets, battery, now)
            outlook = project(initial, settings, steps, forecast_used=True)
            end = (
                outlook.takeover_at
                if kind == "evening"
                else datetime.combine(day, time(22), tzinfo=zone).astimezone(UTC)
            )
            if end is None or (kind == "evening" and outlook.lowest_pct is None):
                record["result"]["reason_code"] = "no_night"
                continue
            record.update(
                until=end.isoformat(),
                soc_pct=initial,
                settings={"reserve_pct": battery.reserve_pct, "target_pct": battery.target_pct},
                published={
                    "lowest_pct": round(outlook.lowest_pct, 1)
                    if outlook.lowest_pct is not None
                    else None,
                    "reserve_at": outlook.reserve_at.isoformat() if outlook.reserve_at else None,
                    "full_at": outlook.full_at.isoformat() if outlook.full_at else None,
                },
            )
            record["result"] = grade(
                record,
                soc,
                buckets,
                full_observed_at=_timed_full(soc, now, end, battery.target_pct),
            )
    return rows


def evaluate(
    data,
    *,
    installation,
    zone_name,
    kind="unverified",
    provider=None,
    keying="unverified",
    period_end_before=None,
    soc=None,
):
    """One installation only. No shared learning or calibration across inputs."""
    data = unwrap(data)
    zone = ZoneInfo(zone_name)
    battery = _battery(data)
    buckets = diagnostics.buckets_from(data["window"])
    if not buckets:
        raise ValueError("measurement window is empty")
    if any(b.start_utc.tzinfo is None for b in buckets):
        raise ValueError("measurement timestamps must include UTC offsets")
    history = data.get("forecast_history", {})
    choices = [key for key, value in history.items() if value.get("dayahead")]
    if provider is None and len(choices) != 1:
        raise ValueError("select --provider when the download has zero or multiple archives")
    selected = provider if provider is not None else choices[0]
    if selected not in choices:
        raise ValueError("selected provider has no day-ahead archive")
    forecast = replay.forecast_from_diagnostics({selected: history[selected]})
    assert forecast is not None
    if not forecast or any(
        t.tzinfo is None or not math.isfinite(v) or v < 0 for t, v in forecast.items()
    ):
        raise ValueError("forecast must contain finite nonnegative energy at aware timestamps")
    if keying == "period-end" and period_end_before is not None:
        raise ValueError("choose period-end or a mixed-keying cutoff, not both")
    if keying == "period-end":
        forecast = {t - timedelta(hours=1): v for t, v in forecast.items()}
    if period_end_before is not None:
        forecast = replay.keyed_by_period_start(forecast, period_end_before)
    soc = soc if soc is not None else replay.soc_from_diagnostics(data.get("soc_history", []))
    if not soc:
        raise ValueError("download has no charge observations")
    context = hashlib.sha256(
        json.dumps(
            {
                "battery": asdict(battery),
                "zone": zone_name,
                "channels": sorted(
                    (c.get("role"), c.get("entity_id"))
                    for c in data.get("entry", {}).get("data", {}).get("channels", [])
                ),
                "forecast_keying": keying,
                "period_end_before": str(period_end_before),
                "provider": selected,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    previous = (replay.RAW, replay.ONE_FACTOR, replay.SHAPE_SHADOW, replay.DAYLIGHT_WINDOWS)
    try:
        replay.RAW = replay.ONE_FACTOR = replay.SHAPE_SHADOW = False
        replay.DAYLIGHT_WINDOWS = {
            date.fromisoformat(day): tuple(datetime.fromisoformat(t) for t in bounds)
            for day, bounds in data.get("daylight_windows", {}).items()
        }
        baseline = _historical(
            data, buckets, soc, forecast, battery=battery, zone=zone, context=context
        )
        replay.SHAPE_SHADOW = True
        candidate = _historical(
            data,
            buckets,
            soc,
            forecast,
            battery=battery,
            zone=zone,
            context=context,
            candidate=True,
        )
    finally:
        replay.RAW, replay.ONE_FACTOR, replay.SHAPE_SHADOW, replay.DAYLIGHT_WINDOWS = previous
    since = buckets[0].start_utc - timedelta(days=1)
    base_summary = summarize(baseline, context, since)
    by_id = {r["id"]: r for r in candidate if r["result"]["status"] == "graded"}
    paired = [r for r in baseline if r["id"] in by_id and r["result"]["status"] == "graded"]
    paired_candidate = [by_id[r["id"]] for r in paired]
    journal = data.get("outlook_journal", [])
    groups = {}
    revisions: dict[str, set[str]] = {}
    for row in journal:
        if row.get("model_revision"):
            revisions.setdefault(row["context"], set()).add(row["model_revision"])
    for row in journal:
        known = revisions.get(row["context"], set())
        revision = row.get("model_revision") or (
            next(iter(known)) if len(known) == 1 else "unknown"
        )
        group = (row["context"], revision)
        groups.setdefault(group, []).append(row)
    issued = [
        {
            "context": ctx,
            "model_revision": revision,
            "summary": summarize(rows, ctx, since),
            "prospective_shape_gate": shape_gate(rows, ctx),
        }
        for (ctx, revision), rows in sorted(groups.items())
    ]
    aligned = all((t.minute, t.second, t.microsecond) == (0, 0, 0) for t in forecast)
    timing_verified = aligned and (keying != "unverified" or period_end_before is not None)
    kinds: dict[str, int] = {}
    for channel in data.get("coverage", {}).get("channels", []):
        channel_kind = channel.get("kind") or "unknown"
        kinds[channel_kind] = kinds.get(channel_kind, 0) + 1
    gates = {
        "distinct_complete_nights": base_summary["nights"] >= 14,
        "night_mae": base_summary["night_lowest_mae"] is not None
        and base_summary["night_lowest_mae"] <= 5,
        "reserve_accuracy": base_summary["reserve_call_accuracy"] is not None
        and base_summary["reserve_call_accuracy"] >= 85,
        "provider_timing_verified": timing_verified,
    }
    return {
        "schema_version": 1,
        "installation": installation,
        "installation_kind": kind,
        "context": context,
        "time_zone": zone_name,
        "battery": asdict(battery),
        "window": {
            "from": buckets[0].start_utc.isoformat(),
            "until": (buckets[-1].start_utc + timedelta(hours=1)).isoformat(),
        },
        "channel_kinds": kinds,
        "export_mapped": "grid_export" in data["window"]["keys"],
        "independent_daylight_windows": len(data.get("daylight_windows", {})),
        "provider_timing": {
            "keying": "mixed" if period_end_before is not None else keying,
            "hour_aligned": aligned,
            "period_end_before": period_end_before.isoformat() if period_end_before else None,
            "lag_correlations": replay.forecast_lag(buckets, forecast),
        },
        "historical_baseline": {
            "revision": BASELINE,
            **base_summary,
            "excluded_by_checkpoint": {
                kind: summarize([r for r in baseline if r["checkpoint"] == kind], context, since)[
                    "excluded"
                ]
                for kind in ("morning", "evening")
            },
        },
        "historical_shape_comparison": {
            "revision": SHAPE_REVISION,
            "evidence_kind": "historical_development_only",
            "candidate_eligibility": summarize(candidate, context, since),
            "baseline": summarize(paired, context, since),
            "candidate": summarize(paired_candidate, context, since),
        },
        "issued_journal_groups": issued,
        "night_accuracy_gates": gates,
        "night_accuracy_gate_passed": all(gates.values()),
        "interval_revision": INTERVAL_REVISION,
        "generalization_proven": False,
        "independent_provenance_review_required": True,
        "timing_resolution": "hour" if soc.hourly_extremes else "recorded_state",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("diagnostics", type=Path)
    parser.add_argument("--installation", required=True, help="private installation label")
    parser.add_argument("--tz", required=True)
    parser.add_argument(
        "--kind",
        choices=["unverified", "reference", "independent", "synthetic"],
        default="unverified",
    )
    parser.add_argument("--provider", help="provider archive key, required if several exist")
    parser.add_argument(
        "--keying", choices=["unverified", "period-start", "period-end"], default="unverified"
    )
    parser.add_argument("--period-end-before", type=datetime.fromisoformat)
    parser.add_argument("--soc-csv", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.period_end_before is not None and args.period_end_before.tzinfo is None:
        parser.error("--period-end-before must include a UTC offset")
    try:
        report = evaluate(
            diagnostics.load(args.diagnostics),
            installation=args.installation,
            zone_name=args.tz,
            kind=args.kind,
            provider=args.provider,
            keying=args.keying,
            period_end_before=args.period_end_before,
            soc=replay.soc_from_csv(args.soc_csv) if args.soc_csv else None,
        )
    except (ValueError, KeyError) as error:
        parser.error(str(error))
    report["source_sha256"] = hashlib.sha256(args.diagnostics.read_bytes()).hexdigest()
    rendered = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Wrote private evaluation: {args.output}")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
