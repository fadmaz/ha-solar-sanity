"""Release-reviewed decisions that may unlock a subsequent prospective experiment."""

# The daylight-shape experiment has not finished. A future reviewed release may
# replace None with a documented decision (id, closed_at, model_revision, outcome,
# evidence). See docs/OPEN_THREADS.md. Selecting weekdays cannot bypass this gate.
MORNING_DECISION: dict | None = None
