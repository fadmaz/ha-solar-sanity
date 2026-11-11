"""Options and display values for the battery projection's power controls."""

import math
from collections.abc import Mapping

from .analysis.outlook import BatteryControls
from .const import OPT_BATTERY_CHARGE_POWER_LIMIT_W, OPT_INVERTER_STANDBY_W

CONTROL_KEYS = (OPT_BATTERY_CHARGE_POWER_LIMIT_W, OPT_INVERTER_STANDBY_W)


def power_override(value: object, *, positive: bool) -> float:
    """Validate a manual value in watts. Zero standby is an explicit setting."""
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError("Enter a number in watts.")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError("Enter a finite number in watts.") from error
    if not math.isfinite(number) or number < 0 or (positive and number == 0):
        raise ValueError(
            "Enter a finite positive power." if positive else "Enter a finite non-negative power."
        )
    return number


def manual_controls(options: Mapping[str, object]) -> dict[str, float]:
    """Read explicit values only, keeping the original identity for Auto."""
    values = {}
    for key in CONTROL_KEYS:
        raw = options.get(key)
        if raw is None:
            continue
        try:
            values[key] = power_override(raw, positive=key == OPT_BATTERY_CHARGE_POWER_LIMIT_W)
        except ValueError:
            continue
    return values


def control_view(controls: BatteryControls) -> dict:
    return {
        key: {
            "manual_w": manual,
            "effective_w": effective,
            "source": "manual"
            if manual is not None
            else "learned"
            if learned is not None
            else "default",
        }
        for key, manual, learned, effective in (
            (
                OPT_BATTERY_CHARGE_POWER_LIMIT_W,
                controls.manual_limit_w,
                controls.learned_limit_w,
                controls.limit_w,
            ),
            (
                OPT_INVERTER_STANDBY_W,
                controls.manual_standby_w,
                controls.learned_standby_w,
                0.0 if controls.standby_w is None else controls.standby_w,
            ),
        )
    }
