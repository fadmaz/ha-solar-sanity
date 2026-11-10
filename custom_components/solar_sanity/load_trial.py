"""Persist only the optional experiment's epoch; production context stays unchanged."""

import hashlib
import json
from datetime import datetime

from . import experiment_policy
from .analysis.load_experiment import load_gate
from .analysis.load_schedule import WEEKDAYS
from .const import OPT_LOAD_PROFILE_ALTERNATE_DAYS


class LoadTrial:
    def __init__(self, journal):
        self.journal = journal
        self.control: dict = {}

    @property
    def days(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                set(self.journal.coordinator.entry.options.get(OPT_LOAD_PROFILE_ALTERNATE_DAYS, []))
            )
        )

    def _identity(self) -> str:
        payload = [self.journal.context(), self.days, experiment_policy.MORNING_DECISION]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def sync(self, now: datetime) -> bool:
        identity = self._identity()
        if self.control.get("identity") == identity:
            return False
        generation = self.control.get("generation", 0) + 1
        self.control = {
            "version": 1,
            "identity": identity,
            "generation": generation,
            "started_at": now.isoformat(),
            "context": hashlib.sha256(
                f"{identity}:{generation}:{now.isoformat()}".encode()
            ).hexdigest(),
        }
        return True

    def context(self) -> str | None:
        return (
            self.control.get("context")
            if self.control.get("identity") == self._identity()
            else None
        )

    def blocked(self, now: datetime) -> str | None:
        from .journal import MODEL_REVISION

        if not self.days:
            return "load_schedule_not_selected"
        if not set(self.days) <= set(WEEKDAYS) or len(self.days) == 7:
            return "load_schedule_invalid"
        if not self.journal.writable:
            return "journal_unavailable"
        decision = experiment_policy.MORNING_DECISION
        if not isinstance(decision, dict) or not all(
            isinstance(decision.get(key), str) and decision[key]
            for key in ("id", "closed_at", "model_revision", "outcome", "evidence")
        ):
            return "morning_experiment_open"
        if decision["model_revision"] != MODEL_REVISION:
            return "production_revision_changed"
        if decision["outcome"] not in ("baseline_retained", "candidate_promoted"):
            return "morning_experiment_open"
        try:
            closed = datetime.fromisoformat(decision["closed_at"])
            if closed.tzinfo is None or closed > now:
                return "morning_experiment_open"
        except ValueError:
            return "morning_experiment_open"
        if self.journal.coordinator.time_zone is None:
            return "time_zone_unavailable"
        if self.context() is None:
            return "load_schedule_reset_pending"
        return None

    def summary(self, now: datetime) -> dict:
        from .journal import MODEL_REVISION

        view = load_gate(
            self.journal.records,
            self.context(),
            self.journal.context(),
            MODEL_REVISION,
            since=datetime.fromisoformat(self.control["started_at"]) if self.control else now,
            until=now,
        )
        reason = self.blocked(now)
        view.update(
            selected_days=list(self.days),
            reason_code=reason or "collecting_pairs",
            collecting=reason is None,
            started_at=self.control.get("started_at"),
            context=self.context(),
        )
        if reason:
            view["ready_for_review"] = False
        return view


def valid_control(value) -> bool:
    """Unknown experiment metadata is preserved by the journal's restore guard."""
    if not isinstance(value, dict) or value.get("version") != 1:
        return False
    try:
        return (
            isinstance(value["generation"], int)
            and not isinstance(value["generation"], bool)
            and value["generation"] >= 1
            and all(isinstance(value[k], str) and value[k] for k in ("identity", "context"))
            and datetime.fromisoformat(value["started_at"]).tzinfo is not None
        )
    except (KeyError, TypeError, ValueError):
        return False
