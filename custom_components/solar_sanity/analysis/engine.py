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
from .linalg import median, pearson, sum_squares
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
    TopologyEstimate,
)
from .residual import (
    MIN_SIGNAL_WH,
    DayResidual,
    band_counts,
    build_days,
    median_daily_abs_pct,
    median_daily_pct,
    total_abs_residual,
)
from .topology import Closure

#: Consecutive actionable days before we will look for an explanation at all.
MIN_ACTIONABLE_DAYS = 5

#: Clean days out of the last seven that mean "say nothing".
CLEAN_DAYS_FOR_OK = 6

#: Below this in both channels, an hour is telling us nothing about whether two
#: sensors track each other. Two channels that are asleep agree perfectly.
TRACKING_MIN_WH = 25.0

#: How far the unexplained energy may differ from a channel, as a share of that
#: channel, before it stops being that channel.
#:
#: The counterfactual alone is a magnitude test: it asks whether the house is
#: out by about one of these, not whether the missing energy *is* this one. Two
#: real strings on a roof each answer yes the moment an unrelated fault happens
#: to be roughly their size, and the engine then tells somebody to unmap half
#: their generation. Found by an adversarial sweep at one house in sixty.
#:
#: Measured: a duplicated sensor sits at 0.000 with clean data and 0.056 with
#: ten per cent of independent error on both devices, while two real strings
#: beside an unmetered draw sit at 0.459 to 0.543. An order of magnitude apart,
#: so this sits three times above the worst duplicate and half the best
#: adversary.
DUPLICATE_MAX_MISMATCH = 0.20

#: How closely a pair must move together before we will say in so many words
#: that they track each other.
#:
#: Not a detection threshold — the counterfactual decides that, and it decides
#: it without reference to how the pair looks. This exists because the sentence
#: shown to the user quotes the figure, and a claim we print had better be true.
#: Measured on the synthetic house, two sensors watching one flow correlate at
#: 0.997 with realistic per-device error and still at 0.968 when each is given
#: ten per cent of independent noise, so the margin here is wide.
TRACKING_MIN_CORRELATION = 0.90


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

    # --- Stage 0: is one of our own overrides the problem? --------------------
    # Ahead of everything, because every stage below reads buckets this has
    # already altered — the screens most of all, and they return first.
    #
    # Left out, the failure is not a missed diagnosis but a wrong instruction. A
    # user whose battery sensor reads backwards accepts the flip offered here;
    # months later their integration ships a polarity option and they fix it
    # properly. The override now inverts a correct sensor, and this engine told
    # them "Battery charging is reporting backwards", advised them to wrap it in
    # a template that negates it, and offered a second flip on top of the first.
    if stale := _stale_corrections(request, specs):
        return AnalysisReport(
            status=Status.FAULT_FOUND,
            finding=_harmful_correction(stale[0], specs),
            stale_corrections=stale,
        )

    # --- Stage A: categorical facts, before anything statistical -------------
    # A channel we ourselves inverted reads negative in every hour, which is
    # precisely what the backwards-sensor screen is looking for. Telling the
    # user their sensor is wired backwards, when an override they accepted here
    # is what turned it around, is our own doing reported as their fault —
    # complete with advice to negate it a second time.
    flipped = {
        correction.channel_key
        for correction in request.active_corrections
        if correction.kind == "sign_flip"
    }
    hits = [
        hit
        for hit in screen.run_all(buckets, specs, request.live_snapshots)
        if hit.code not in request.suppressed_codes
        and not (hit.code == Code.CHANNEL_NEVER_POSITIVE and set(hit.channel_keys) <= flipped)
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
    if _would_be_ok(days):
        return AnalysisReport(
            status=Status.OK,
            # The figures behind the verdict. A diagnostics download is most
            # often asked for by somebody wanting to check a verdict rather than
            # dispute it, and "OK" with nothing under it cannot be checked.
            measurements=_measurements(days, specs),
            topology=estimate,
            loss_model=loss,
            residual=summary,
        )

    # Before the bands get a say — for storage only, and for a reason. See
    # _structural_finding.
    if closure.state is Closure.OPEN:
        structural = _structural_finding(
            request, specs, days, summary=summary, estimate=estimate, loss=loss
        )
        if structural is not None:
            return structural

    if sum(1 for d in recent if d.band == "actionable") < MIN_ACTIONABLE_DAYS:
        restricted = _restricted_report(
            request,
            specs,
            buckets,
            loss=loss,
            estimate=estimate,
            full_days=days,
            closure=closure,
        )
        if restricted is not None:
            return restricted
        return AnalysisReport(
            status=Status.INVESTIGATING,
            reason=_with_closure(
                "The numbers move around but not consistently enough to name.", closure
            ),
            notes=_draw_note(days, specs),
            measurements=_measurements(days, specs),
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
    best = scored[0] if scored else None

    if best is None or not hypotheses.passes_gates(best, len(days)):
        # Only now does the counterfactual earn what it costs. If the ordinary
        # path has something to say, asking what would happen without each
        # channel in turn cannot change the answer — and it is the most
        # expensive thing this engine does, a loss-model refit per channel.
        #
        # Both things it can tell us live here because both are the same
        # failure: two explanations that score alike, which the margin gate
        # answers with silence. When two channels each settle the house neither
        # can be singled out and the pair is the finding. When exactly one does,
        # the tie was never real and the hypothesis naming it was right all
        # along.
        wants_pair = Code.DUPLICATE_CHANNEL not in request.suppressed_codes
        settled = (
            _interchangeable_channels(request, specs, buckets)
            if wants_pair or _could_be_rescued(best, len(days))
            else {}
        )

        if wants_pair and (pair := _duplicate_pair(specs, buckets, days, candidates, settled)):
            return AnalysisReport(
                status=Status.FAULT_FOUND,
                finding=pair,
                identity_fails=True,
                topology=estimate,
                loss_model=loss,
                residual=summary,
                measurements=_measurements(days, specs),
            )

        if _rescued_by_the_counterfactual(best, settled, len(days)):
            return AnalysisReport(
                status=Status.FAULT_FOUND,
                identity_fails=True,
                finding=_render_hypothesis(best, specs, days, summary),
                deferred=tuple(h.code for h in scored[1:3]),
                topology=estimate,
                loss_model=loss,
                residual=summary,
            )

        restricted = _restricted_report(
            request,
            specs,
            buckets,
            loss=loss,
            estimate=estimate,
            full_days=days,
            closure=closure,
        )
        if restricted is not None:
            return restricted
        return AnalysisReport(
            status=Status.INVESTIGATING,
            # Reached only after the identity has been shown to miss by more
            # than a tenth of throughput on most of the last week. That is a
            # data problem we are certain of; only its cause is open.
            identity_fails=True,
            reason=_with_closure(_unattributed_reason(days, scored), closure),
            notes=_draw_note(days, specs),
            measurements=_measurements(days, specs),
            deferred=tuple(h.code for h in scored[:3]),
            topology=estimate,
            loss_model=loss,
            residual=summary,
        )

    return AnalysisReport(
        status=Status.FAULT_FOUND,
        identity_fails=True,
        finding=_render_hypothesis(best, specs, days, summary),
        # The evidence, beside the accusation. This is the verdict most worth
        # being able to audit, and it was the one carrying no numbers at all.
        measurements=_measurements(days, specs),
        deferred=tuple(h.code for h in scored[1:3]),
        topology=estimate,
        loss_model=loss,
        residual=summary,
    )


def _structural_finding(
    request: AnalysisRequest,
    specs: tuple[ChannelSpec, ...],
    days: tuple[DayResidual, ...],
    *,
    summary: ResidualSummary,
    estimate: TopologyEstimate,
    loss: LossModel,
) -> AnalysisReport | None:
    """An unmeasured *store*, if one is convincing.

    Only storage is exempted from the daily bands, and the exemption is not a
    lower bar — it is because the bands measure the wrong thing here. A band
    asks how far a day's residual runs in one direction, and a store borrows in
    the afternoon and repays at night, so its net is near zero however much
    energy is moving. No band will ever call that actionable.

    An unmeasured export path has no such problem: it runs one way all day, the
    bands measure it exactly as intended, and it stays behind them. Where they
    keep it quiet, they are keeping it quiet for the right reason.
    """
    candidates = [
        c
        for c in hypotheses.generate_structural(days, specs)
        if c.code == Code.MISSING_STORAGE and c.code not in request.suppressed_codes
    ]
    if not candidates:
        return None

    scored = hypotheses.score(days, candidates)
    if not scored or not hypotheses.passes_gates(scored[0], len(days)):
        return None

    return AnalysisReport(
        status=Status.FAULT_FOUND,
        identity_fails=True,
        finding=_render_hypothesis(scored[0], specs, days, summary),
        deferred=tuple(h.code for h in scored[1:3]),
        topology=estimate,
        loss_model=loss,
        residual=summary,
    )


def _restricted_report(
    request: AnalysisRequest,
    specs: tuple[ChannelSpec, ...],
    buckets: tuple[Bucket, ...],
    *,
    loss: LossModel,
    estimate: TopologyEstimate,
    full_days: tuple[DayResidual, ...],
    closure: topology.ClosureResult,
) -> AnalysisReport | None:
    """A verdict from the hours in which nothing can leave unmeasured.

    On a house with no export meter, the energy that appears to be missing in a
    surplus hour and the energy that actually left are the same number — there
    is no measurement that separates them, and no amount of waiting produces
    one. Reporting "still looking" is then a promise that cannot be kept.

    But the hours either side of that are ordinary arithmetic. With no
    generation there is nothing to export, so the identity closes, and import
    plus discharge really does have to equal consumption. That is a real
    verdict about a real part of the system, and it is the difference between
    saying something true and saying nothing for as long as the house stands.

    Returns ``None`` when even those hours are too few, so the caller keeps its
    own answer.
    """
    if not closure.unmeasured_export:
        return None

    days = build_days(buckets, specs, loss, request.utc_offset_hours, verifiable_only=True)
    if len(days) < MIN_ACTIONABLE_DAYS:
        return None

    summary = _summarise(days)
    notes = _unverifiable_notes(days, full_days) + _draw_note(days, specs)
    recent = days[-7:]

    common = {
        "notes": notes,
        "measurements": _measurements(days, specs),
        "topology": estimate,
        "loss_model": loss,
        "residual": summary,
    }

    if sum(1 for d in recent if d.band == "clean") >= min(CLEAN_DAYS_FOR_OK, len(recent)):
        return AnalysisReport(status=Status.OK, **common)

    if sum(1 for d in recent if d.band == "actionable") < MIN_ACTIONABLE_DAYS:
        return AnalysisReport(
            status=Status.INVESTIGATING,
            reason="The numbers move around but not consistently enough to name.",
            **common,
        )

    if total_abs_residual(recent) < MIN_SIGNAL_WH:
        return AnalysisReport(
            status=Status.INSUFFICIENT_DATA,
            reason="Not enough energy in play to attribute the difference.",
            **common,
        )

    # The boundary is closed *within these hours*, so the probes that exist to
    # explain an open one have nothing to offer and must not be generated.
    candidates = [
        c for c in hypotheses.generate(days, specs, False) if c.code not in request.suppressed_codes
    ]
    scored = hypotheses.score(days, candidates)

    if not scored or not hypotheses.passes_gates(scored[0], len(days)):
        return AnalysisReport(
            status=Status.INVESTIGATING,
            identity_fails=True,
            reason=_unattributed_reason(days, scored),
            deferred=tuple(h.code for h in scored[:3]),
            **common,
        )

    return AnalysisReport(
        status=Status.FAULT_FOUND,
        identity_fails=True,
        finding=_render_hypothesis(scored[0], specs, days, summary),
        deferred=tuple(h.code for h in scored[1:3]),
        **common,
    )


def _measurements(
    days: tuple[DayResidual, ...], specs: tuple[ChannelSpec, ...]
) -> dict[str, float]:
    """Everything measured on the way to saying nothing.

    A rejected term leaves 0.0 behind and reports the same empty tuple whether
    the slope it saw was a quarter or a rounding error. Those are completely
    different problems, and without the numbers the only way to tell them apart
    is to add logging and ship again.
    """
    out: dict[str, float] = {}
    signed = median_daily_pct(days)
    if signed is not None:
        out["median_daily_pct_signed"] = signed
    for band, count in band_counts(days).items():
        out[f"days_{band}"] = float(count)
    out.update(topology.night_fit_raw(days, specs))
    return out


def _draw_note(days: tuple[DayResidual, ...], specs: tuple[ChannelSpec, ...]) -> tuple[str, ...]:
    """Report a continuous unmetered draw rather than absorbing it.

    The loss model refuses anything larger than an inverter idles at, which is
    right — quietly subtracting a kilowatt-hour a day as "normal" would hide the
    thing the user most needs told. But having measured it and said nothing is
    no better.
    """
    watts = topology.unmetered_draw_w(days, specs)
    if watts is None:
        return ()
    return (
        f"Something draws about {watts:.0f} W continuously that nothing measures "
        f"— roughly {watts * 24 / 1000:.1f} kWh a day. That is more than an "
        "inverter's own idle draw, so it is a load rather than a rounding error.",
    )


def _unverifiable_notes(
    days: tuple[DayResidual, ...], full_days: tuple[DayResidual, ...]
) -> tuple[str, ...]:
    """Say exactly what this verdict does and does not cover."""
    hours = sum(len(day.buckets) for day in days) / len(days)
    notes = [
        f"Nothing measures what leaves your house, so only the {hours:.0f} hours "
        "a day with no generation could be checked — in those, nothing can be "
        "exported and the arithmetic has to close. Your generation sensor is "
        "not covered by this, because it only produces energy during the hours "
        "that cannot be checked."
    ]

    surplus = _surplus_kwh_per_day(full_days)
    if surplus is not None and surplus > 0.1:
        notes.append(
            f"About {surplus:.1f} kWh a day is unaccounted for while you have a "
            "surplus. With no export meter that is most likely what you are "
            "sending to the grid, but it cannot be told apart from a generation "
            "sensor reading high."
        )
    return tuple(notes)


def _surplus_kwh_per_day(days: tuple[DayResidual, ...]) -> float | None:
    """Energy unaccounted for in hours when generation exceeded consumption."""
    if not days:
        return None
    total = 0.0
    for day in days:
        for surplus, value in zip(hypotheses.surplus_mask(day), day.dr, strict=True):
            if surplus and value > 0:
                total += value
    return total / len(days) / 1000.0


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
                local_date=bucket.local_date,
            )
        )
    return tuple(out)


def _harmful_correction(channel_key: str, specs: tuple[ChannelSpec, ...]) -> Finding:
    """Ask for an override to be taken off, naming the channel it sits on."""
    spec = _spec_for(specs, channel_key)
    name = spec.friendly_name if spec is not None else channel_key
    headline, detail, fix = faults.render(Code.CORRECTION_NOW_HARMFUL, name=name)
    return Finding(
        code=Code.CORRECTION_NOW_HARMFUL,
        severity=Severity.FAULT,
        confidence=Confidence.HIGH,
        channel_keys=(channel_key,),
        headline=headline,
        detail=detail,
        source_fix=fix,
        # Emphatically none. The remedy is to remove an override, and offering
        # to apply another one here is how this went wrong in the first place.
        offered_correction=None,
    )


def _without_channels(buckets: tuple[Bucket, ...], keys: frozenset[str]) -> tuple[Bucket, ...]:
    """The same buckets with these channels contributing nothing.

    Zero rather than absent, because a missing reading and a reading of zero
    are different facts everywhere else in this engine and a bucket with a
    channel missing is not a valid bucket. This is exactly what the
    ``drop_channel`` correction does, which is the point — the question being
    asked is whether that correction would work.
    """
    return tuple(
        Bucket(
            start_utc=bucket.start_utc,
            seconds=bucket.seconds,
            wh={key: (0.0 if key in keys else value) for key, value in bucket.wh.items()},
            quality=bucket.quality,
            source=bucket.source,
            solar_elevation_deg=bucket.solar_elevation_deg,
            is_dst_transition=bucket.is_dst_transition,
            local_date=bucket.local_date,
        )
        for bucket in buckets
    )


def _closes_without(
    request: AnalysisRequest,
    specs: tuple[ChannelSpec, ...],
    buckets: tuple[Bucket, ...],
    key: str,
) -> tuple[bool, tuple[DayResidual, ...]]:
    """Whether dropping this one channel would settle the whole installation.

    Returns the days it judged along with the verdict, because the caller needs
    to say by how much and rebuilding them to find out would double the cost of
    the most expensive thing this engine does.

    ``buckets`` arrive with the user's corrections already applied, because that
    is the installation as this engine sees it — asking the question against the
    raw readings would be asking about a house nobody is looking at.
    """
    without = _without_channels(buckets, frozenset({key}))
    provisional = build_days(
        without, specs, request.loss_model or LossModel(), request.utc_offset_hours
    )
    loss = topology.fit_loss_model(provisional, specs, request.loss_model)
    settled = build_days(without, specs, loss, request.utc_offset_hours)
    return _would_be_ok(settled), settled


def _tracking(buckets: tuple[Bucket, ...], first: str, second: str) -> float | None:
    """How closely two channels move together, over the hours either is doing
    anything. Night hours agree perfectly about nothing and would flatter any
    pair, so they are left out."""
    xs: list[float] = []
    ys: list[float] = []
    for bucket in buckets:
        # `value`, not `wh`, because this is a statistic and not a raw-stream
        # screen. A single reset-suspect hour carrying a counter artefact is
        # discarded everywhere else in the package; read raw it drags the
        # correlation from 1.00 to -0.03, which is below the floor below, which
        # silences a correct finding on a house that is out by a third.
        a = bucket.value(first)
        b = bucket.value(second)
        if a is None or b is None:
            continue
        if abs(a) < TRACKING_MIN_WH and abs(b) < TRACKING_MIN_WH:
            continue
        xs.append(a)
        ys.append(b)
    return pearson(xs, ys)


def _residual_mismatch(
    days: tuple[DayResidual, ...], specs: tuple[ChannelSpec, ...], key: str
) -> float | None:
    """How far the unexplained energy is from being exactly this channel.

    Over the hours the channel is doing something, compare what is missing with
    what the channel contributed: zero means the residual *is* this channel,
    hour for hour, which is what a second sensor on the same flow produces.

    Deliberately not the gamma estimate the snap table uses. Gamma needs a
    channel busy in a quarter of its hours and battery charging and grid export
    are not, so it cannot answer this question for half the roles a duplicate
    can land in. This one only ever looks at the channel's own active hours, so
    a channel that runs four hours a day is judged on those four.
    """
    spec = _spec_for(specs, key)
    if spec is None:
        return None

    deviations: list[float] = []
    magnitudes: list[float] = []
    for day in days:
        for bucket, dr in zip(day.buckets, day.dr, strict=True):
            value = bucket.value(key)
            if value is None or abs(value) < TRACKING_MIN_WH:
                continue
            contribution = spec.role.sign * value
            deviations.append(abs(dr - contribution))
            magnitudes.append(abs(contribution))

    typical = median(magnitudes)
    spread = median(deviations)
    if typical is None or spread is None or typical <= 0:
        return None
    return spread / typical


def _interchangeable_channels(
    request: AnalysisRequest,
    specs: tuple[ChannelSpec, ...],
    buckets: tuple[Bucket, ...],
) -> dict[str, tuple[DayResidual, ...]]:
    """Channels that would settle the whole installation if dropped, and the
    days each of those counterfactuals produced.

    Every channel in the balance, not only the ones a gamma estimate accused.
    Gamma needs a channel busy in at least a quarter of its hours, and battery
    charging and grid export are not: they run a few hours a day, so the
    upper-quartile cutoff comes out at zero and no estimate is ever produced.
    Gating on that missed a duplicated charge or export sensor entirely — half
    the roles a duplicate can land in.

    Stops at three, because nothing above it uses more than two and each of
    these costs a loss-model refit.
    """
    settled: dict[str, tuple[DayResidual, ...]] = {}
    for key in sorted(spec.key for spec in specs if spec.role.in_balance):
        closes, without = _closes_without(request, specs, buckets, key)
        if closes:
            settled[key] = without
            if len(settled) > 2:
                return {}
    return settled


def _rescued_by_the_counterfactual(
    best: Hypothesis | None,
    settled: dict[str, tuple[DayResidual, ...]],
    days_evaluated: int,
) -> bool:
    """Whether the top hypothesis is right and only the margin disagrees.

    A near-copy of a channel — a second sensor reading a few per cent off its
    partner — makes the two DOUBLE_COUNTED hypotheses score within a hundredth
    of each other, and the margin gate wants fifteen hundredths. So the correct
    hypothesis, naming the correct channel, sat at the top of the list and was
    rejected for being insufficiently better than the wrong one. Between 0.90
    and 0.95 of the original the engine said nothing at all about a house that
    was out by a third.

    The counterfactual settles what the margin cannot. Dropping that one channel
    makes the whole installation add up and dropping the other does not, which
    is not a closer score — it is a different answer.

    Deliberately narrow: only the margin may be outstanding, only one channel
    may close, and it has to be the one the hypothesis names. Restricted to
    DOUBLE_COUNTED because that is the claim the evidence makes — "dropping this
    channel settles everything" means the channel contributes nothing real,
    which is what double counting is. It says nothing about a channel being
    backwards or half-read, so it may not rescue those.
    """
    return _could_be_rescued(best, days_evaluated) and (
        best is not None and len(settled) == 1 and best.channel_keys == tuple(settled)
    )


def _could_be_rescued(best: Hypothesis | None, days_evaluated: int) -> bool:
    """Everything `_rescued_by_the_counterfactual` asks that does not need the
    counterfactual to have been run.

    Separate so the scan can be skipped entirely. It is the most expensive thing
    this engine does, and if the top hypothesis is not the kind this evidence
    could speak to, running it answers a question nobody asked.
    """
    if best is None or best.code != Code.DOUBLE_COUNTED:
        return False
    return hypotheses.gate_failures(best, days_evaluated) == {hypotheses.GATE_MARGIN}


def _duplicate_pair(
    specs: tuple[ChannelSpec, ...],
    buckets: tuple[Bucket, ...],
    days: tuple[DayResidual, ...],
    candidates: list[Hypothesis],
    settled: dict[str, tuple[DayResidual, ...]],
) -> Finding | None:
    """Two channels that are one flow counted twice, named as a pair.

    The test is a counterfactual rather than a resemblance, and that matters
    more than it sounds. Two sensors on the same array correlate at 1.00 with a
    ratio of 1.00 — and so do two genuinely separate strings of equal size on
    the same roof. Measured on the synthetic house, those two cases are
    identical to four decimal places on every statistic of the channels
    themselves. Nothing about how the pair *looks* can separate them.

    What separates them is what happens if you take one away. Drop one of two
    real strings and half the generation goes missing; drop one of two sensors
    watching the same string and the balance closes. So the first question asked
    here is the one that has an answer: would dropping this channel, on its own,
    settle the installation? When that is true of two channels, neither can be
    singled out — they are interchangeable.

    That on its own is a magnitude test, and not enough. It asks whether the
    house is out by about one of these, not whether the missing energy *is* one
    of these — and two real strings both answer yes the moment an unrelated
    fault happens to be roughly their size. So the second question is the
    identity one: is the unexplained energy this channel, hour for hour? A
    duplicated sensor answers to within a few per cent; two real strings beside
    an unmetered draw are out by half.

    Being self-gating is the useful part of the first test. It fires only when
    the pair is the entire story: if anything else were also wrong, removing one
    channel would not leave a clean house.
    """
    # Exactly two. One means the ordinary path has a channel to name — and now
    # says so even when the margin gate would have stopped it, see
    # `_rescued_by_the_counterfactual`. Three or more means no pair is the
    # answer, and with three copies of one flow it is unreachable rather than
    # merely unhandled: dropping any single one still leaves the house out by a
    # third, so none of them closes it.
    if len(settled) != 2:
        return None
    interchangeable = list(settled)

    # Interchangeable, but is either of them actually the missing energy? Two
    # real strings pass the test above whenever something unrelated is about
    # their size; neither passes this one.
    for key in interchangeable:
        mismatch = _residual_mismatch(days, specs, key)
        if mismatch is None or mismatch > DUPLICATE_MAX_MISMATCH:
            return None

    # Is the pair the only thing that fits? When some channel outside it also
    # snaps to a fault, two physical explanations are on the table.
    #
    # Not hypothetical. Two real battery banks beside a load CT on one of two
    # live conductors collide exactly: at night the battery carries the house,
    # so what is missing is half the load, and each bank contributes half the
    # load. The residual *is* one bank's output, hour for hour, to machine
    # precision. Both tests above pass and the engine would tell somebody to
    # unmap a real battery — while the CT sits there reading half.
    #
    # There is no separating the two. A genuine duplicate of the load channel
    # throws a competing candidate of exactly the same shape, so refusing to
    # speak whenever one exists would cost real findings and buy nothing. What
    # it is good for is knowing how much to claim: with a rival explanation in
    # play this is a question rather than a conclusion, and the copy then says
    # so instead of instructing somebody to unmap a sensor.
    contested = any(
        key not in set(interchangeable) for hyp in candidates for key in hyp.channel_keys
    )

    first, second = interchangeable
    correlation = _tracking(buckets, first, second)
    if correlation is None:
        # The copy quotes this figure, and there is no honest stand-in for a
        # number that could not be computed.
        return None
    if not correlation >= TRACKING_MIN_CORRELATION:
        # Interchangeable in the balance, but not moving together — so whatever
        # these two are, "the same energy measured twice" is not a description
        # of it, and that is the only sentence available here.
        return None

    names = [_spec_for(specs, key) for key in (first, second)]
    if any(spec is None for spec in names):
        return None

    # How much of the mismatch this accounts for, measured the same way the
    # scored hypotheses measure it, so the two are comparable in diagnostics.
    # Left absent rather than defaulted when it cannot be computed: a finding
    # that explains nothing and a finding whose share is unknown are different
    # facts, and this project has a suite that says so.
    baseline = sum_squares([value for day in days for value in day.dr])
    explained = 0.0
    evidence: tuple[Evidence, ...] = ()
    if baseline > 0:
        remaining = sum_squares([value for day in settled[first] for value in day.dr])
        explained = 1.0 - (remaining / baseline)
        evidence = (
            Evidence(
                label="Mismatch this pair accounts for",
                value=explained * 100.0,
                unit="%",
                window_days=len(days),
            ),
            Evidence(
                label="How closely the two track each other",
                value=correlation * 100.0,
                unit="%",
                window_days=len(days),
            ),
        )

    headline, detail, fix = faults.render(
        Code.DUPLICATE_CHANNEL,
        name=names[0].friendly_name,
        other=names[1].friendly_name,
        correlation=correlation,
    )
    # The same two downgrades every other finding takes. A pair inferred from
    # channels this integration guessed at, or from hourly means rather than our
    # own integration, is not as certain as one from channels the user mapped
    # and readings we took — and asserting it at full confidence anyway is the
    # inconsistency the screen path was already fixed for.
    confidence = Confidence.HIGH
    if contested:
        confidence = confidence.downgrade()
    if any(spec.autodetected for spec in names if spec is not None):
        confidence = confidence.downgrade()
    if _rests_on_means(buckets, (first, second)):
        confidence = confidence.downgrade()

    return Finding(
        code=Code.DUPLICATE_CHANNEL,
        severity=Severity.QUESTION if confidence is Confidence.PROBABLE else Severity.FAULT,
        confidence=confidence,
        channel_keys=(first, second),
        headline=headline,
        detail=detail,
        source_fix=fix,
        # None, and not for want of a candidate. Dropping either one would close
        # the balance, so an override here would be this engine picking which of
        # the user's two sensors to silence on the strength of a coin toss.
        offered_correction=None,
        evidence=evidence,
        explained_fraction=explained,
        # The counterfactual is evaluated over the whole window rather than
        # sampled, so every day supports it or none does.
        days_supporting=len(days),
        days_evaluated=len(days),
    )


def _would_be_ok(days: tuple[DayResidual, ...]) -> bool:
    """The verdict test, in one place so nothing can disagree with it."""
    recent = days[-7:]
    if not recent:
        return False
    return sum(1 for d in recent if d.band == "clean") >= min(CLEAN_DAYS_FOR_OK, len(recent))


def _days_for(
    request: AnalysisRequest,
    specs: tuple[ChannelSpec, ...],
    corrections: tuple[Correction, ...],
) -> tuple[DayResidual, ...]:
    """What this installation would look like under exactly these corrections.

    The loss model is refitted rather than reused, because it is *derived* from
    the buckets: asking what the numbers would look like without an override
    means asking with the loss those buckets imply, not with one fitted against
    the override still in place.
    """
    buckets = _apply_corrections(request.buckets, corrections)
    provisional = build_days(
        buckets, specs, request.loss_model or LossModel(), request.utc_offset_hours
    )
    loss = topology.fit_loss_model(provisional, specs, request.loss_model)
    return build_days(buckets, specs, loss, request.utc_offset_hours)


def _stale_corrections(request: AnalysisRequest, specs: tuple[ChannelSpec, ...]) -> tuple[str, ...]:
    """Corrections that now make things worse than they would be without them.

    A correction is an override on our own copy of a channel, and the user is
    told it is applied "so I can keep checking" — never that anything is fixed.
    So the underlying sensor usually does get fixed eventually: the integration
    ships a polarity option, or a template gets rewritten. At that moment our
    override stops compensating for a fault and becomes one, inverting a sensor
    that is now correct. Nothing else would ever notice. The residual simply
    goes wrong again and stays wrong, and the user has our own past advice on
    file saying they already dealt with it.

    The test is the verdict itself rather than a threshold invented for the
    occasion: a correction is stale when dropping it would make this
    installation ``ok`` and keeping it would not.

    Each trial refits the loss model, since that is what the counterfactual
    actually means — see ``_days_for``.
    """
    corrections = request.active_corrections
    if not corrections:
        return ()
    if _would_be_ok(_days_for(request, specs, corrections)):
        return ()

    stale: list[str] = []
    for correction in corrections:
        others = tuple(c for c in corrections if c is not correction)
        if _would_be_ok(_days_for(request, specs, others)):
            stale.append(correction.channel_key)
    return tuple(stale)


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


def _snap_fields(
    hyp: Hypothesis, specs: tuple[ChannelSpec, ...], days: tuple[DayResidual, ...]
) -> dict[str, object]:
    """Fields a snap-table template needs that the hypothesis does not carry.

    Screens compute these while they work; the inferred path never did, so four
    of the six snap entries — both half-coverage variants and both unit-scale
    variants — raised ``KeyError`` from ``faults.render`` at the moment they
    won. In Home Assistant that surfaces as the whole integration going
    unavailable, on exactly the installations it most needed to help.
    """
    if hyp.a is None or not hyp.channel_keys:
        return {}

    key = hyp.channel_keys[0]
    fields: dict[str, object] = {}

    if hyp.code == Code.PARTIAL_COVERAGE:
        fields["fraction"] = _as_fraction(hyp.a)
    elif hyp.code == Code.UNIT_SCALE_1000:
        observed, expected = _magnitudes(days, specs, key)
        if observed is None or expected is None:
            # Better to say nothing than to invent a comparison. The caller's
            # render will raise, which the template-coverage test exists to
            # make impossible, but a fabricated number would ship silently.
            return {}
        fields["observed"] = observed
        fields["expected"] = expected

    return fields


def _as_fraction(a: float) -> str:
    """How much of the truth a channel with this correction factor is seeing."""
    if abs(a - 2.0) < 0.01:
        return "half"
    if abs(a - 3.0) < 0.01:
        return "a third"
    return f"1/{a:.0f}"


def _magnitudes(
    days: tuple[DayResidual, ...], specs: tuple[ChannelSpec, ...], key: str
) -> tuple[float | None, float | None]:
    """Typical hourly size of one channel, and of the others it sits beside."""
    own: list[float] = []
    others: list[float] = []
    for day in days:
        for bucket in day.buckets:
            for spec in specs:
                if not spec.role.in_balance:
                    continue
                value = bucket.value(spec.key)
                if value is None or value <= 0:
                    continue
                (own if spec.key == key else others).append(value)
    return median(own), median(others)


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
        fields.update(_snap_fields(hyp, specs, days))
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
