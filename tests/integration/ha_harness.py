"""The same test helpers from the harness installed for each Core version."""

from importlib.util import find_spec

if find_spec("pytest_homeassistant") is not None:
    from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed
    from pytest_homeassistant.components.recorder.common import (
        async_wait_recording_done,
        statistics_during_period,
    )
else:
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
        async_fire_time_changed,
    )
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
        statistics_during_period,
    )

__all__ = [
    "MockConfigEntry",
    "async_fire_time_changed",
    "async_wait_recording_done",
    "statistics_during_period",
]
