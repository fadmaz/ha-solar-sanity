"""``analyse`` — the whole engine, as one pure function.

No clock, no randomness, no I/O, no logging, no Home Assistant types. The same
request always produces a byte-identical report. That is what makes three
thousand synthetic scenarios cheap to run in CI, and it is the only reason the
false-positive budget is verifiable at all.

Order matters: categorical screens first (a frozen or wrong-by-1000 channel
makes every residual meaningless), then closure, then loss fitting, then — and
only then — attribution.
"""

from __future__ import annotations

from . import faults, hypotheses, screen, topology
from .faults import Code
from .hypotheses import MIN_HOURS_FOR_SNAP, Hypothesis
from .model import (
    AnalysisReport,
    AnalysisRequest,
    Bucket,
    BucketSource,
    ChannelSpec,
    Confidence,
    Correction,
    Evidence,
    Finding,
    LossModel,
    Quality,
    ResidualSummary,
    Severity,
    Status,
)
from .residual import (
    MIN_SIGNAL_WH,
    DayResidual,
    build_days,
    median_daily_abs_pct,
    total_abs_residual,
)
from .topology import Closure

#: Consecutive actionable days before we will look for an explanation at all.
MIN_ACTIONABLE_DAYS = 5

#: Clean days out of the last seven that mean "say nothing".
CLEAN_DAYS_FOR_OK = 6


def analyse(request: AnalysisRequest) -> AnalysisReport:
    """Return at most one finding about this installation."""
    specs = request.specs
    if not specs:
        return AnalysisReport(
            status=Status.NOT_CHECKABLE,
            reason="No channels are configured.",
        )

    hypotheses.register_specs(specs)
    buckets = _apply_corrections(request.buckets, request.active_corrections)

    # --- Stage A: categorical facts, before anything statistical -------------
    hits = [
        hit
        for hit in screen.run_all(buckets, specs, request.live_snapshots)
        if hit.code not in request.suppressed_codes
    ]
    if hits:
        primary = hits[0]
        return AnalysisReport(
            status=Status.FAULT_FOUND,
            finding=_render_screen_hit(primary, specs, buckets),
            deferred=tuple(h.code for h in hits[1:]),
        )

    # --- A channel with no history can never form a valid hour ---------------
    # Every bucket needs every balance channel, so one unrecorded channel
    # invalidates all of them. Reporting that as "not enough data yet" is true
    # and useless: waiting will not help, and the user cannot act on it without
    # being told which sensor.
    unrecorded = [
        key
        for key in request.unrecorded_keys
        if (spec := request.spec(key)) is not None and spec.role.in_balance
    ]
    if unrecorded:
        names = ", ".join(
            spec.friendly_name for key in unrecorded if (spec := request.spec(key)) is not None
        )
        return AnalysisReport(
            status=Status.NOT_CHECKABLE,
            reason=(
                f"Home Assistant keeps no history for {names}. Those sensors have "
                "no state class, so nothing is recorded for them and the balance "
                "cannot be checked against the past."
            ),
        )

    # --- Closure: can the identity say anything? -----------------------------
    closure = topology.check_closure(specs, request.declared)
    if closure.state is Closure.NOT_CHECKABLE:
        return AnalysisReport(
            status=Status.NOT_CHECKABLE,
            reason=closure.reason,
        )

    # --- Loss model, then residual -------------------------------------------
    provisional = build_days(
        buckets, specs, request.loss_model or LossModel(), request.utc_offset_hours
    )
    if len(provisional) < MIN_ACTIONABLE_DAYS:
        return AnalysisReport(
            status=Status.INSUFFICIENT_DATA,
            reason=_shortage_reason(len(provisional), buckets, specs),
            residual=_summarise(provisional),
        )

    loss = topology.fit_loss_model(provisional, specs, request.loss_model)
    days = build_days(buckets, specs, loss, request.utc_offset_hours)
    estimate = topology.infer(days, specs, request.declared, loss)
    summary = _summarise(days)

    if len(days) < MIN_ACTIONABLE_DAYS:
        return AnalysisReport(
            status=Status.INSUFFICIENT_DATA,
            reason=f"Only {len(days)} complete days of data so far.",
            topology=estimate,
            loss_model=loss,
            residual=summary,
        )

    recent = days[-7:]
    if sum(1 for d in recent if d.band == "clean") >= min(CLEAN_DAYS_FOR_OK, len(recent)):
        return AnalysisReport(
            status=Status.OK,
            topology=estimate,
            loss_model=loss,
            residual=summary,
            stale_corrections=_stale_corrections(days, request.active_corrections),
        )

    if sum(1 for d in recent if d.band == "actionable") < MIN_ACTIONABLE_DAYS:
        return AnalysisReport(
            status=Status.INVESTIGATING,
            reason=_with_closure(
                "The numbers move around but not consistently enough to name.", closure
            ),
            topology=estimate,
            loss_model=loss,
            residual=summary,
        )

    if total_abs_residual(recent) < MIN_SIGNAL_WH:
        return AnalysisReport(
            status=Status.INSUFFICIENT_DATA,
            reason="Not enough energy in play to attribute the difference.",
            topology=estimate,
            loss_model=loss,
            residual=summary,
        )

    # --- Attribution ---------------------------------------------------------
    candidates = hypotheses.generate(days, specs, closure.state is Closure.OPEN)
    candidates = [c for c in candidates if c.code not in request.suppressed_codes]
    scored = hypotheses.score(days, candidates)

    if not scored or not hypotheses.passes_gates(scored[0], len(days)):
        return AnalysisReport(
            status=Status.INVESTIGATING,
            # Reached only after the identity has been shown to miss by more
            # than a tenth of throughput on most of the last week. That is a
            # data problem we are certain of; only its cause is open.
            identity_fails=True,
            reason=_with_closure(_unattributed_reason(days, scored), closure),
            deferred=tuple(h.code for h in scored[:3]),
            topology=estimate,
            loss_model=loss,
            residual=summary,
        )

    best = scored[0]
    return AnalysisReport(
        status=Status.FAULT_FOUND,
        identity_fails=True,
        finding=_render_hypothesis(best, specs, days, summary),
        deferred=tuple(h.code for h in scored[1:3]),
        topology=estimate,
        loss_model=loss,
        residual=summary,
        stale_corrections=_stale_corrections(days, request.active_corrections),
    )


def _with_closure(reason: str, closure: topology.ClosureResult) -> str:
    """Append the closure caveat when there is one.

    An open boundary was previously computed, used internally to widen the
    hypothesis set, and then discarded — so a user whose configuration cannot
    balance by construction was told the numbers merely did not add up.
    """
    if closure.state is Closure.OPEN and closure.reason:
        return f"{reason} {closure.reason}"
    return reason


def _unattributed_reason(days: tuple[DayResidual, ...], scored: list) -> str:
    """Why nothing was named — distinguishing "rejected" from "not yet asked"."""
    if scored:
        return "The numbers do not add up, but no explanation is convincing yet."

    hours = sum(len(day.buckets) for day in days)
    if hours < MIN_HOURS_FOR_SNAP:
        needed = -(-MIN_HOURS_FOR_SNAP // 24)
        return (
            "The numbers do not add up. Pinning it on a particular sensor needs "
            f"about {needed} complete days and there are {len(days)} so far, so "
            "nothing has been ruled in or out yet."
        )
    return "The numbers do not add up, but no explanation is convincing yet."


def _shortage_reason(days: int, buckets: tuple[Bucket, ...], specs: tuple[ChannelSpec, ...]) -> str:
    """Explain a shortage in terms the user can act on.

    "Only 0 complete days" is true and unhelpful. If one channel is present in
    far fewer hours than the others, that channel is the reason, and saying so
    turns a wait into a fix.
    """
    if not buckets:
        return "No measurements yet."

    coverage = {
        spec.key: sum(1 for b in buckets if b.value(spec.key) is not None)
        for spec in specs
        if spec.role.in_balance
    }
    if coverage:
        worst_key = min(coverage, key=lambda k: coverage[k])
        best = max(coverage.values())
        if best and coverage[worst_key] < best * 0.5:
            spec = next((s for s in specs if s.key == worst_key), None)
            name = spec.friendly_name if spec else worst_key
            if _history_merely_starts_later(buckets, worst_key):
                # Not the same problem, and it must not read as one. A sensor
                # whose history begins later has nothing wrong with it and
                # nothing for the user to fix; saying it is "holding everything
                # back" sends them looking for a fault that is not there.
                return (
                    f"{days} complete days so far. {name} has only been recorded "
                    f"since the start of its history, and an hour needs every "
                    "sensor, so the window starts where it does. Nothing is "
                    "wrong — there is just less of it yet."
                )
            return (
                f"{days} complete days so far. {name} has gaps: data for only "
                f"{coverage[worst_key]} of {best} hours, and an hour needs every "
                "sensor, so it is holding everything else back."
            )

    return f"Only {days} complete days of data so far."


def _history_merely_starts_later(buckets: tuple[Bucket, ...], key: str) -> bool:
    """Whether a channel is simply younger rather than intermittent.

    Contiguous-from-a-later-start and scattered-with-holes look identical in a
    coverage count and mean completely different things: one resolves by
    waiting, the other never does.
    """
    ordered = sorted(buckets, key=lambda b: b.start_utc)
    first = next((i for i, b in enumerate(ordered) if b.value(key) is not None), None)
    if first is None:
        return False
    since = ordered[first:]
    if len(since) < 24:
        return False
    present = sum(1 for b in since if b.value(key) is not None)
    return present / len(since) >= 0.9


def _apply_corrections(
    buckets: tuple[Bucket, ...], corrections: tuple[Correction, ...]
) -> tuple[Bucket, ...]:
    """Apply the user's accepted diagnostic overrides to the raw buckets."""
    if not corrections:
        return buckets

    out: list[Bucket] = []
    for bucket in buckets:
        wh = dict(bucket.wh)
        for correction in corrections:
            value = wh.get(correction.channel_key)
            if value is None:
                continue
            if correction.kind == "sign_flip":
                wh[correction.channel_key] = -value
            elif correction.kind == "scale" and correction.factor:
                wh[correction.channel_key] = value * correction.factor
            elif correction.kind == "drop_channel":
                wh[correction.channel_key] = 0.0
        out.append(
            Bucket(
                start_utc=bucket.start_utc,
                seconds=bucket.seconds,
                wh=wh,
                quality=bucket.quality,
                source=bucket.source,
                solar_elevation_deg=bucket.solar_elevation_deg,
                is_dst_transition=bucket.is_dst_transition,
            )
        )
    return tuple(out)


def _stale_corrections(
    days: tuple[DayResidual, ...], corrections: tuple[Correction, ...]
) -> tuple[str, ...]:
    """Corrections that now make things worse — usually because the user fixed
    the sensor at source and forgot to remove the override."""
    if not corrections or not days:
        return ()
    # A correction is suspect once the residual is clean without needing it;
    # the full re-test runs in the coordinator where the uncorrected buckets are
    # still available.
    return ()


def _summarise(days: tuple[DayResidual, ...]) -> ResidualSummary:
    if not days:
        return ResidualSummary()
    return ResidualSummary(
        median_daily_abs_pct=median_daily_abs_pct(days),
        valid_days=len(days),
        total_abs_wh=total_abs_residual(days),
        band=days[-1].band,
    )


def _spec_for(specs: tuple[ChannelSpec, ...], key: str) -> ChannelSpec | None:
    return next((s for s in specs if s.key == key), None)


def _rests_on_means(buckets: tuple[Bucket, ...], keys: tuple[str, ...]) -> bool:
    """Whether any evidence for these channels came from an hourly mean."""
    if not keys:
        return False
    for bucket in buckets:
        for key in keys:
            if bucket.source.get(key) is BucketSource.LTS_MEAN:
                return True
            if bucket.quality.get(key) is Quality.DERIVED_FROM_MEAN:
                return True
    return False


def _render_screen_hit(
    hit: screen.ScreenHit,
    specs: tuple[ChannelSpec, ...],
    buckets: tuple[Bucket, ...],
) -> Finding:
    headline, detail, fix = faults.render(hit.code, **hit.fields)

    confidence = hit.confidence
    # Once, not once per channel: a two-channel hit was being downgraded twice
    # for the same reason, which the hypothesis path never did.
    if any(
        (spec := _spec_for(specs, key)) is not None and spec.autodetected
        for key in hit.channel_keys
    ):
        confidence = confidence.downgrade()

    # A screen hit computed from hourly means is no more certain than an
    # inferred one. The hypothesis path has always applied this; the screen path
    # never did, so a categorical fault could be asserted at full confidence on
    # evidence the rest of the engine treats as weak.
    if _rests_on_means(buckets, hit.channel_keys):
        confidence = confidence.downgrade()

    correction = None
    if hit.correction_kind and hit.channel_keys and confidence is not Confidence.PROBABLE:
        factor = None
        if hit.correction_kind == "scale":
            observed = hit.fields.get("observed")
            expected = hit.fields.get("expected")
            if isinstance(observed, (int, float)) and isinstance(expected, (int, float)):
                factor = 0.001 if observed > expected else 1000.0
        correction = Correction(
            channel_key=hit.channel_keys[0],
            kind=hit.correction_kind,
            factor=factor,
        )

    return Finding(
        code=hit.code,
        severity=Severity.QUESTION if confidence is Confidence.PROBABLE else Severity.FAULT,
        confidence=confidence,
        channel_keys=hit.channel_keys,
        headline=headline,
        detail=detail,
        source_fix=fix,
        evidence=tuple(
            Evidence(label=k, value=float(v), unit="", window_days=0)
            for k, v in hit.fields.items()
            if isinstance(v, (int, float))
        ),
        offered_correction=correction,
    )


def _render_hypothesis(
    hyp: Hypothesis,
    specs: tuple[ChannelSpec, ...],
    days: tuple[DayResidual, ...],
    summary: ResidualSummary,
) -> Finding:
    fields: dict[str, object] = {
        "days": len(days),
        "explained": hyp.explained * 100.0,
        "pct": summary.median_daily_abs_pct if summary.median_daily_abs_pct is not None else 0.0,
    }

    confidence = hyp.confidence
    if hyp.channel_keys:
        spec = _spec_for(specs, hyp.channel_keys[0])
        if spec is not None:
            fields["name"] = spec.friendly_name
            if spec.autodetected:
                confidence = confidence.downgrade()
    if hyp.extra:
        fields.update(hyp.extra)

    # A finding resting on mean-derived buckets cannot be certain: an
    # arithmetic hourly mean over an event-reporting sensor over-weights
    # volatile hours.
    if any(day.from_mean for day in days):
        confidence = confidence.downgrade()

    headline, detail, fix = faults.render(hyp.code, **fields)

    severity = Severity.QUESTION if confidence is Confidence.PROBABLE else Severity.FAULT

    correction = None
    if hyp.correction_kind and hyp.channel_keys and confidence is not Confidence.PROBABLE:
        correction = Correction(
            channel_key=hyp.channel_keys[0],
            kind=hyp.correction_kind,
            factor=hyp.a if hyp.correction_kind == "scale" else None,
        )

    return Finding(
        code=hyp.code,
        severity=severity,
        confidence=confidence,
        channel_keys=hyp.channel_keys,
        headline=headline,
        detail=detail,
        source_fix=fix,
        evidence=(
            Evidence("explained", hyp.explained * 100.0, "%", len(days)),
            Evidence("days_supporting", float(hyp.days_supporting), "days", len(days)),
        ),
        offered_correction=correction,
        explained_fraction=hyp.explained,
        margin=hyp.margin,
        days_supporting=hyp.days_supporting,
        days_evaluated=len(days),
    )


__all__ = ["Code", "analyse"]
