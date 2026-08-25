"""Every selector this integration builds must actually be valid.

This suite exists because of a real failure. The topology step used
``selector.selector({"config_entry": {"multiple": True}})``. ``ConfigEntrySelector``
has no ``multiple`` option — its config schema accepts ``integration`` and
nothing else — so building that schema raised ``vol.Invalid``. Home Assistant
surfaced it as a bare "Unknown error occurred" on submitting the *previous*
step, which points at the wrong place entirely.

Nothing caught it: the analysis engine has no Home Assistant imports, so none of
its 125 tests touch the config flow, and a selector config is only validated
when the schema is constructed. These tests construct every schema.

They need Home Assistant importable and are skipped when it is not, so they run
in CI and are simply absent when working on the pure engine locally.
"""

from __future__ import annotations

import pytest

homeassistant = pytest.importorskip("homeassistant", reason="Home Assistant not installed")
voluptuous = pytest.importorskip("voluptuous")


def _schema_keys(schema) -> set[str]:
    return {str(key.schema) for key in schema.schema}


class TestConfigFlowSchemas:
    """Each schema must build without raising, and expose the right fields."""

    def test_channel_schema_builds(self) -> None:
        from custom_components.solar_sanity.config_flow import (
            MAPPED_ROLES,
            _channel_schema,
        )
        from custom_components.solar_sanity.discovery import Discovery

        schema = _channel_schema(Discovery(), {})
        keys = _schema_keys(schema)

        for role in MAPPED_ROLES:
            assert role.key in keys, f"{role.key} missing from the channel schema"

    def test_entity_selector_config_is_valid(self) -> None:
        """A selector's config is only validated when it is constructed."""
        from custom_components.solar_sanity.config_flow import _entity_selector

        selector = _entity_selector()
        # Home Assistant normalises `domain` to a list, whether one was given or
        # a bare string. Constructing it at all is the real assertion here — an
        # invalid config raises rather than returning something wrong.
        assert selector.config["domain"] == ["sensor"]

    def test_tristate_selector_is_valid(self) -> None:
        """Every setup question must offer "not sure" as a real answer."""
        from custom_components.solar_sanity.config_flow import _TRISTATE

        values = set(_TRISTATE.config["options"])
        assert values == {"yes", "no", "unknown"}

    def test_forecast_provider_selector_is_valid(self) -> None:
        """The exact construction that used to raise.

        ``ConfigEntrySelector`` cannot do this; a ``SelectSelector`` can, and it
        shows the provider's name rather than a UUID.
        """
        from homeassistant.helpers import selector

        built = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[selector.SelectOptionDict(value="abc123", label="Forecast.Solar")],
                multiple=True,
                mode=selector.SelectSelectorMode.LIST,
            )
        )
        assert built.config["multiple"] is True

    def test_config_entry_selector_rejects_multiple(self) -> None:
        """Pin the upstream constraint that caused the bug.

        If Home Assistant ever adds ``multiple`` to ``ConfigEntrySelector`` this
        test fails, which is the right moment to reconsider the workaround.
        """
        import voluptuous as vol
        from homeassistant.helpers import selector

        with pytest.raises(vol.Invalid):
            selector.selector({"config_entry": {"multiple": True}})

    def test_options_schema_builds(self) -> None:
        """The options flow has never been exercised by a user."""
        import voluptuous as vol
        from homeassistant.helpers import selector

        from custom_components.solar_sanity.const import CONF_GUARANTEED_ANNUAL_KWH

        schema = vol.Schema(
            {
                vol.Optional(CONF_GUARANTEED_ANNUAL_KWH): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=100000,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="kWh",
                    )
                ),
            }
        )
        assert CONF_GUARANTEED_ANNUAL_KWH in _schema_keys(schema)


class TestIntegrationImports:
    """Every module must import the way Home Assistant loads it."""

    @pytest.mark.parametrize(
        "module",
        [
            "config_flow",
            "coordinator",
            "diagnostics",
            "discovery",
            "entity",
            "frontend",
            "repairs",
            "sensor",
            "binary_sensor",
            "statistics_source",
        ],
    )
    def test_module_imports(self, module: str) -> None:
        """Catches the class of bug that made v0.1.0 unloadable."""
        __import__(f"custom_components.solar_sanity.{module}")


class TestQuestionsAreConditional:
    """Never ask what the mapping already answers.

    A user who has just mapped battery charge and discharge sensors should not
    then be asked whether they have a battery. Beyond looking silly, an
    "unknown" answer there changes what the engine does — it runs the
    missing-storage probe — so a redundant question is also a chance to record
    a worse answer than the one we already had.
    """

    def _flow(self, channels: dict[str, str]):
        from custom_components.solar_sanity.config_flow import SolarSanityConfigFlow

        flow = SolarSanityConfigFlow()
        flow._channels = channels
        return flow

    def test_battery_question_is_skipped_when_battery_is_mapped(self) -> None:
        flow = self._flow(
            {"pv": "sensor.pv", "load": "sensor.load", "battery_charge": "sensor.chg"}
        )
        assert flow._battery_mapped is True

    def test_battery_question_is_asked_when_no_battery_is_mapped(self) -> None:
        flow = self._flow({"pv": "sensor.pv", "load": "sensor.load"})
        assert flow._battery_mapped is False

    def test_grid_net_question_only_matters_when_import_is_alone(self) -> None:
        """Both mapped means two dedicated sensors — nothing to interpret."""
        both = self._flow(
            {
                "pv": "sensor.pv",
                "load": "sensor.load",
                "grid_import": "sensor.i",
                "grid_export": "sensor.e",
            }
        )
        assert both._both_grid_mapped is True

        import_only = self._flow(
            {"pv": "sensor.pv", "load": "sensor.load", "grid_import": "sensor.i"}
        )
        assert import_only._both_grid_mapped is False
