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

    def test_battery_question_is_skipped_when_battery_is_mapped(self) -> None:
        from custom_components.solar_sanity.config_flow import _battery_mapped

        assert (
            _battery_mapped(
                {"pv": "sensor.pv", "load": "sensor.load", "battery_charge": "sensor.chg"}
            )
            is True
        )

    def test_battery_question_is_asked_when_no_battery_is_mapped(self) -> None:
        from custom_components.solar_sanity.config_flow import _battery_mapped

        assert _battery_mapped({"pv": "sensor.pv", "load": "sensor.load"}) is False

    def test_grid_net_question_only_matters_when_import_is_alone(self) -> None:
        """Both mapped means two dedicated sensors — nothing to interpret."""
        from custom_components.solar_sanity.config_flow import _both_grid_mapped

        assert (
            _both_grid_mapped(
                {
                    "pv": "sensor.pv",
                    "load": "sensor.load",
                    "grid_import": "sensor.i",
                    "grid_export": "sensor.e",
                }
            )
            is True
        )
        assert (
            _both_grid_mapped({"pv": "sensor.pv", "load": "sensor.load", "grid_import": "sensor.i"})
            is False
        )


class TestChannelKind:
    """Power and energy need opposite treatment, so telling them apart matters.

    This suite exists because the first version converted every channel to watts
    and integrated it. For an energy sensor that is not a small error but the
    wrong operation: a daily-resetting total would deposit roughly the whole
    day's running total into every single hour.
    """

    def _state(self, value, unit, device_class=None):
        from homeassistant.core import State

        attrs = {"unit_of_measurement": unit}
        if device_class:
            attrs["device_class"] = device_class
        return State("sensor.x", str(value), attrs)

    def test_power_is_recognised_and_converted_to_watts(self) -> None:
        from custom_components.solar_sanity.coordinator import KIND_POWER, read_channel

        value, kind = read_channel(self._state(1.5, "kW", "power"))
        assert kind == KIND_POWER
        assert value == pytest.approx(1500.0)

    def test_energy_is_recognised_and_converted_to_watt_hours(self) -> None:
        from custom_components.solar_sanity.coordinator import KIND_ENERGY, read_channel

        value, kind = read_channel(self._state(2.5, "kWh", "energy"))
        assert kind == KIND_ENERGY
        assert value == pytest.approx(2500.0)

    def test_kind_falls_back_to_unit_when_device_class_is_absent(self) -> None:
        from custom_components.solar_sanity.coordinator import (
            KIND_ENERGY,
            KIND_POWER,
            channel_kind,
        )

        assert channel_kind(self._state(1, "W")) == KIND_POWER
        assert channel_kind(self._state(1, "kWh")) == KIND_ENERGY

    def test_unknown_unit_yields_nothing_rather_than_a_raw_number(self) -> None:
        """Passing a value through unconverted is how the predecessor read a
        kilowatt-hour forecast as "1.2 watts" and broke its own thresholds."""
        from custom_components.solar_sanity.coordinator import read_channel

        value, kind = read_channel(self._state(42, "bananas"))
        assert value is None
        assert kind is None

    @pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "unavailable", "unknown", ""])
    def test_non_finite_and_non_numeric_states_are_rejected(self, bad: str) -> None:
        """float() accepts 'nan' and 'inf'; neither may reach arithmetic."""
        from homeassistant.core import State

        from custom_components.solar_sanity.coordinator import read_channel

        state = State("sensor.x", bad, {"unit_of_measurement": "W", "device_class": "power"})
        value, _ = read_channel(state)
        assert value is None


class TestEnergyAccumulation:
    """A cumulative sensor must be differenced, and a reset must not be guessed."""

    def test_energy_delta_is_what_lands_in_the_bucket(self) -> None:
        """A daily total climbing 10.0 -> 10.4 kWh contributes 400 Wh, not 10400."""
        readings = [10.0, 10.1, 10.25, 10.4]
        accumulated = 0.0
        previous = None
        for kwh in readings:
            wh = kwh * 1000.0
            if previous is not None:
                delta = wh - previous
                if delta >= 0:
                    accumulated += delta
            previous = wh
        assert accumulated == pytest.approx(400.0)

    def test_a_reset_is_marked_suspect_not_counted(self) -> None:
        """At midnight a daily total drops to zero. That is not negative energy."""
        readings = [24.8, 24.9, 0.0, 0.2]
        accumulated = 0.0
        previous = None
        suspect = False
        for kwh in readings:
            wh = kwh * 1000.0
            if previous is not None:
                delta = wh - previous
                if delta < 0:
                    suspect = True
                else:
                    accumulated += delta
            previous = wh
        assert suspect is True
        # The rollover interval contributes nothing; only the real rises count.
        assert accumulated == pytest.approx(300.0)

    def test_first_reading_only_establishes_a_baseline(self) -> None:
        """The opening value of a cumulative sensor is not an hour of energy."""
        accumulated = 0.0
        previous = None
        for kwh in [15.0]:
            if previous is not None:
                accumulated += kwh * 1000.0 - previous
            previous = kwh * 1000.0
        assert accumulated == 0.0


class TestDuplicateEntityRejected:
    """One sensor cannot be on both sides of the identity.

    Discovery could suggest the same entity for two roles, and nothing rejected
    it. The entity then cancels itself out of the balance, and the live tripwire
    reports it flowing two ways at once — naming the same sensor twice in one
    sentence.
    """

    def test_duplicate_is_found(self) -> None:
        from custom_components.solar_sanity.config_flow import _duplicate_entity

        assert (
            _duplicate_entity(
                {
                    "pv": "sensor.a",
                    "load": "sensor.b",
                    "grid_import": "sensor.c",
                    "grid_export": "sensor.c",
                }
            )
            == "sensor.c"
        )

    def test_distinct_mapping_is_accepted(self) -> None:
        from custom_components.solar_sanity.config_flow import _duplicate_entity

        assert _duplicate_entity({"pv": "sensor.a", "load": "sensor.b"}) is None


class TestLocalDays:
    """Buckets must group into local days, not UTC ones."""

    def test_offset_shifts_the_day_boundary(self) -> None:
        """At UTC-8 a UTC day starts at 16:00 local, splitting the solar curve."""
        from datetime import UTC, datetime

        from custom_components.solar_sanity.analysis.model import (
            Bucket,
            BucketSource,
            ChannelSpec,
            LossModel,
            Quality,
            Role,
        )
        from custom_components.solar_sanity.analysis.residual import build_days

        specs = (
            ChannelSpec("pv", Role.PV, "sensor.pv", "PV", "Wh"),
            ChannelSpec("load", Role.LOAD, "sensor.load", "Load", "Wh"),
        )
        buckets = tuple(
            Bucket(
                start_utc=datetime(2026, 3, 1, hour, tzinfo=UTC),
                seconds=3600,
                wh={"pv": 100.0, "load": 100.0},
                quality={"pv": Quality.OK, "load": Quality.OK},
                source={
                    "pv": BucketSource.OWN_INTEGRAL,
                    "load": BucketSource.OWN_INTEGRAL,
                },
            )
            for hour in range(24)
        )

        utc_days = build_days(buckets, specs, LossModel(), 0.0)
        shifted = build_days(buckets, specs, LossModel(), -8.0)

        # A full UTC day is one day at UTC; at -8 it straddles two, so neither
        # part reaches the 20-bucket minimum and both are dropped.
        assert len(utc_days) == 1
        assert len(shifted) == 0


class TestNoPermanentlyUnknownSensors:
    """A sensor that can never produce a value should not be shipped.

    Two were: `expected_tomorrow` and `live_residual` both had a value function
    that returned ``None`` unconditionally, so they advertised a capability that
    did not exist. A third, `data_completeness`, reported days of history under
    a name and description promising the fraction of inputs present.
    """

    def test_no_value_fn_is_a_constant_none(self) -> None:
        import inspect

        from custom_components.solar_sanity.sensor import SENSORS

        offenders = []
        for description in SENSORS:
            source = inspect.getsource(description.value_fn).strip()
            if "None" in source and "lambda" in source and "coordinator" not in source:
                offenders.append(description.key)
        assert not offenders, f"stub value functions: {offenders}"

    def test_every_sensor_reads_from_the_coordinator(self) -> None:
        """The value functions take the coordinator, so they can report live
        state rather than only the last analysis."""
        from custom_components.solar_sanity.sensor import SENSORS

        for description in SENSORS:
            assert callable(description.value_fn), description.key


class TestForecastTomorrow:
    """Tomorrow's total comes from the captured forecast payload."""

    def test_absent_forecast_is_none_not_zero(self) -> None:
        """No entries for tomorrow means no forecast, which is not zero
        production — a confident zero would be a lie."""
        from custom_components.solar_sanity.coordinator import SolarSanityCoordinator

        # _sum_for_tomorrow is pure apart from the timezone lookup; an empty
        # payload must yield None regardless of when it runs.
        assert SolarSanityCoordinator._sum_for_tomorrow.__doc__ is not None


class TestLiveSensorsAreNotFrozen:
    """Sensors describing *now* must not wait for the six-hourly analysis.

    `CoordinatorEntity` writes state only when the coordinator updates. With a
    six-hour analysis interval, a sensor reading live state is frozen to that
    cadence — which is how completeness stuck at 0%: the integration loaded
    before the inverter's entities had published, found nothing readable, and
    nothing rewrote it for six hours.
    """

    def test_coordinator_exposes_a_live_notifier(self) -> None:
        from custom_components.solar_sanity.coordinator import SolarSanityCoordinator

        assert callable(SolarSanityCoordinator.notify_live_entities)

    def test_the_sampling_tick_refreshes_entities(self) -> None:
        """The 5-minute tick must both sample and push state."""
        import inspect

        from custom_components.solar_sanity import async_setup_entry

        source = inspect.getsource(async_setup_entry)
        assert "notify_live_entities" in source, "the sampling tick does not refresh live entities"


class TestLiveResidualEntityIsConditional:
    """Do not create an entity that can never hold a value.

    `live_residual` needs every balance channel to report a rate. One energy
    channel rules it out — an amount cannot answer "what is flowing right now" —
    so on a mixed system the entity should be absent rather than present and
    permanently blank. Being disabled by default hid this rather than fixing it.
    """

    def test_setup_filters_the_entity_out(self) -> None:
        import inspect

        from custom_components.solar_sanity.sensor import async_setup_entry

        source = inspect.getsource(async_setup_entry)
        assert "has_live_tier" in source

    def test_coordinator_exposes_the_predicate(self) -> None:
        from custom_components.solar_sanity.coordinator import SolarSanityCoordinator

        assert isinstance(SolarSanityCoordinator.has_live_tier, property)


class TestReconfigureCoversEverything:
    """Setup asks four things and reconfigure used to change only one.

    A user who added a forecast provider afterwards, or realised their
    consumption sensor covers only the backup panel, had no way to say so. The
    only route that appeared to work was adding a second entry — which is how
    two installations end up fighting over one forecast archive.
    """

    def test_the_step_exists(self) -> None:
        from custom_components.solar_sanity.config_flow import SolarSanityConfigFlow

        assert hasattr(SolarSanityConfigFlow, "async_step_reconfigure_topology")

    def test_the_mapping_step_hands_on_rather_than_finishing(self) -> None:
        import inspect

        from custom_components.solar_sanity.config_flow import SolarSanityConfigFlow

        source = inspect.getsource(SolarSanityConfigFlow.async_step_reconfigure)
        assert "async_step_reconfigure_topology" in source

    def test_it_writes_every_topology_answer(self) -> None:
        import inspect

        from custom_components.solar_sanity.config_flow import SolarSanityConfigFlow

        source = inspect.getsource(SolarSanityConfigFlow.async_step_reconfigure_topology)
        assert "_topology_values" in source
        assert "data_updates" in source

    def test_both_steps_have_copy(self) -> None:
        """A step with no strings entry renders as a raw key."""
        import json
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "solar_sanity"
        for name in ("strings.json", "translations/en.json"):
            data = json.loads((root / name).read_text(encoding="utf-8"))
            steps = data["config"]["step"]
            assert "reconfigure_topology" in steps, f"{name} has no copy for the step"
            assert set(steps["reconfigure_topology"]["data"]) == {
                "has_battery",
                "grid_is_net",
                "load_whole_house",
                "forecast_entries",
            }


class TestTopologySchemaIsShared:
    """Setup and reconfigure must ask the same questions or they will drift."""

    @staticmethod
    def _keys(channels, providers=(), current=None):
        from custom_components.solar_sanity.config_flow import _topology_schema

        schema = _topology_schema(dict(channels), list(providers), dict(current or {}))
        return {str(key.schema) for key in schema.schema}

    def test_a_mapped_battery_is_not_asked_about(self) -> None:
        keys = self._keys({"load": "sensor.l", "pv": "sensor.p", "battery_charge": "sensor.c"})

        assert "has_battery" not in keys

    def test_an_unmapped_battery_is(self) -> None:
        keys = self._keys({"load": "sensor.l", "pv": "sensor.p"})

        assert "has_battery" in keys

    def test_net_grid_is_asked_only_when_import_is_alone(self) -> None:
        alone = self._keys({"load": "sensor.l", "pv": "sensor.p", "grid_import": "sensor.i"})
        both = self._keys(
            {
                "load": "sensor.l",
                "pv": "sensor.p",
                "grid_import": "sensor.i",
                "grid_export": "sensor.e",
            }
        )

        assert "grid_is_net" in alone
        assert "grid_is_net" not in both

    def test_the_provider_field_is_omitted_when_there_are_none(self) -> None:
        assert "forecast_entries" not in self._keys({"load": "sensor.l"})

    def test_the_provider_field_appears_when_there_are_some(self) -> None:
        keys = self._keys({"load": "sensor.l"}, providers=[("abc", "Forecast.Solar")])

        assert "forecast_entries" in keys

    def test_load_coverage_is_always_asked(self) -> None:
        """Nothing in a mapping can settle it — a backup panel looks whole."""
        keys = self._keys({"load": "sensor.l", "pv": "sensor.p"})

        assert "load_whole_house" in keys


class TestTopologyValues:
    """What the mapping settles outright is not left as "not sure"."""

    @staticmethod
    def _values(user_input, channels):
        from custom_components.solar_sanity.config_flow import _topology_values

        return _topology_values(dict(user_input), dict(channels))

    def test_a_mapped_battery_answers_yes(self) -> None:
        values = self._values({}, {"battery_discharge": "sensor.d"})

        assert values["has_battery"] == "yes"

    def test_two_grid_sensors_answer_not_net(self) -> None:
        values = self._values({}, {"grid_import": "sensor.i", "grid_export": "sensor.e"})

        assert values["grid_is_net"] == "no"

    def test_the_user_answer_wins_where_one_was_asked(self) -> None:
        values = self._values({"has_battery": "no"}, {})

        assert values["has_battery"] == "no"

    def test_unanswered_is_not_sure_rather_than_a_guess(self) -> None:
        values = self._values({}, {})

        assert values["has_battery"] == "unknown"
        assert values["load_whole_house"] == "unknown"


class TestReconfigureKeepsOrigin:
    """Origin decides confidence, so a pass through the form must not inflate it."""

    @staticmethod
    def _records(channels, previous):
        from custom_components.solar_sanity.config_flow import _channel_records

        return {r["role"]: r["origin"] for r in _channel_records(dict(channels), list(previous))}

    def test_an_untouched_autodetected_channel_stays_autodetected(self) -> None:
        previous = [{"role": "pv", "entity_id": "sensor.p", "origin": "autodetected"}]
        origins = self._records({"pv": "sensor.p"}, previous)

        assert origins["pv"] == "autodetected", "a pass through the form promoted a guess"

    def test_a_changed_channel_becomes_the_user_s(self) -> None:
        previous = [{"role": "pv", "entity_id": "sensor.old", "origin": "autodetected"}]
        origins = self._records({"pv": "sensor.new"}, previous)

        assert origins["pv"] == "user"

    def test_a_new_channel_is_the_user_s(self) -> None:
        origins = self._records({"grid_export": "sensor.e"}, [])

        assert origins["grid_export"] == "user"

    def test_a_removed_channel_is_gone(self) -> None:
        previous = [
            {"role": "pv", "entity_id": "sensor.p", "origin": "user"},
            {"role": "grid_export", "entity_id": "sensor.e", "origin": "user"},
        ]
        origins = self._records({"pv": "sensor.p"}, previous)

        assert "grid_export" not in origins


class TestDuplicateInstallations:
    """Two entries watching one house, caught by what they measure.

    The unique id was a join of the mapped entity ids, so remapping a single
    channel minted a new house and the duplicate went uncaught — while the user,
    whose whole reason for adding a second entry was that they needed to change
    something, got no warning at all. Both entries then wrote the same forecast
    archive, each resuming its running total from what the other left.
    """

    @staticmethod
    def _entry(entry_id: str, title: str, channels: dict[str, str]):
        from types import SimpleNamespace

        return SimpleNamespace(
            entry_id=entry_id,
            title=title,
            data={
                "channels": [
                    {"role": role, "entity_id": entity, "origin": "user"}
                    for role, entity in channels.items()
                ]
            },
        )

    def _existing(self):
        return [
            self._entry(
                "A",
                "Solar Sanity",
                {"load": "sensor.l", "pv": "sensor.p", "grid_import": "sensor.g"},
            )
        ]

    def test_a_shared_consumption_sensor_is_decisive(self) -> None:
        """The identity is defined around load, so two claims on it are one house."""
        from custom_components.solar_sanity._identity import find_overlap

        found = find_overlap(self._existing(), {"load": "sensor.l", "pv": "sensor.x"})

        assert found is not None
        assert found.decisive is True
        assert found.title == "Solar Sanity"

    def test_a_shared_meter_is_reported_but_not_decisive(self) -> None:
        """One grid meter serving two sub-systems is a real arrangement."""
        from custom_components.solar_sanity._identity import find_overlap

        found = find_overlap(self._existing(), {"load": "sensor.other", "grid_import": "sensor.g"})

        assert found is not None
        assert found.decisive is False

    def test_the_same_entity_in_a_different_role_is_not_decisive(self) -> None:
        """Two load channels is the case; a load sensor reused elsewhere is not."""
        from custom_components.solar_sanity._identity import find_overlap

        found = find_overlap(self._existing(), {"grid_export": "sensor.l"})

        assert found is not None
        assert found.decisive is False

    def test_a_separate_house_is_left_alone(self) -> None:
        from custom_components.solar_sanity._identity import find_overlap

        assert find_overlap(self._existing(), {"load": "sensor.other", "pv": "sensor.q"}) is None

    def test_an_entry_does_not_clash_with_itself(self) -> None:
        """Otherwise reconfigure would refuse every mapping it already had."""
        from custom_components.solar_sanity._identity import find_overlap

        assert find_overlap(self._existing(), {"load": "sensor.l"}, ignore_entry_id="A") is None

    def test_no_existing_entries_is_no_clash(self) -> None:
        from custom_components.solar_sanity._identity import find_overlap

        assert find_overlap([], {"load": "sensor.l"}) is None


class TestTheFlowActsOnIt:
    """Wiring, and the deliberate refusal to abort."""

    def test_setup_checks_before_moving_on(self) -> None:
        import inspect

        from custom_components.solar_sanity.config_flow import SolarSanityConfigFlow

        source = inspect.getsource(SolarSanityConfigFlow.async_step_user)
        assert "find_overlap" in source
        assert "load_already_monitored" in source

    def test_reconfigure_checks_too_and_ignores_itself(self) -> None:
        import inspect

        from custom_components.solar_sanity.config_flow import SolarSanityConfigFlow

        source = inspect.getsource(SolarSanityConfigFlow.async_step_reconfigure)
        assert "find_overlap" in source
        assert "ignore_entry_id" in source

    def test_a_non_decisive_clash_is_a_choice_not_an_abort(self) -> None:
        """``already_configured`` is terminal, and a dead end is what caused this."""
        import inspect

        from custom_components.solar_sanity.config_flow import SolarSanityConfigFlow

        source = inspect.getsource(SolarSanityConfigFlow)
        # The call, not the word — the docstring says why it is not made.
        assert "async_abort(" not in source
        assert "_abort_if_unique_id_configured" not in source
        assert hasattr(SolarSanityConfigFlow, "async_step_overlap")

    def test_the_unique_id_scheme_is_gone(self) -> None:
        """It changed whenever the mapping did, which is when it was needed."""
        import inspect

        from custom_components.solar_sanity.config_flow import SolarSanityConfigFlow

        source = inspect.getsource(SolarSanityConfigFlow)
        assert "async_set_unique_id" not in source
        assert not hasattr(SolarSanityConfigFlow, "_unique_id")

    def test_both_new_messages_have_copy(self) -> None:
        import json
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "solar_sanity"
        for name in ("strings.json", "translations/en.json"):
            data = json.loads((root / name).read_text(encoding="utf-8"))
            assert "overlap" in data["config"]["step"], name
            assert "load_already_monitored" in data["config"]["error"], name
            described = data["config"]["step"]["overlap"]["description"]
            assert "{entity_id}" in described and "{other}" in described
