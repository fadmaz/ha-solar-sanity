"""Setup and reconfiguration.

Two things the predecessor could not do, both table stakes for a product whose
whole job is remapping sensors:

* **Reconfigure.** Everything chosen at setup lived in ``entry.data``, which the
  options flow never touched, so correcting a mis-mapped sensor meant deleting
  the entry and orphaning every entity's history. Reconfigure covers the
  forecast providers and the battery too: a setting that can only be changed by
  starting again is how two installations end up fighting over one forecast
  archive.
* **A check that the same house is not configured twice.** Keyed on what the
  entries actually monitor rather than on a unique id: an id derived from the
  mapping changes the moment the mapping does, so remapping a channel minted a
  new house and the duplicate went uncaught.

No update listener is registered. Combining one with
``async_update_reload_and_abort`` or ``OptionsFlowWithReload`` is deprecated as
of 2026.6 and an error from 2026.12, so this takes the listener-free path.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from ._identity import Overlap, find_overlap
from .analysis.load_schedule import WEEKDAYS
from .analysis.model import Role
from .const import (
    CONF_CHANNELS,
    CONF_ENTITY_ID,
    CONF_FORECAST_ENTRIES,
    CONF_ORIGIN,
    CONF_ROLE,
    DEFAULT_RESERVE_PCT,
    DEFAULT_TARGET_PCT,
    DOMAIN,
    OPT_BATTERY_CAPACITY_KWH,
    OPT_BATTERY_POWER,
    OPT_BATTERY_POWER_CHARGING_POSITIVE,
    OPT_BATTERY_RESERVE_PCT,
    OPT_BATTERY_SOC,
    OPT_BATTERY_TARGET_PCT,
    OPT_LOAD_PROFILE_ALTERNATE_DAYS,
)
from .discovery import Discovery, async_discover
from .statistics_source import async_forecast_providers

#: Roles offered at setup, in the order they appear.
MAPPED_ROLES: tuple[Role, ...] = (
    Role.PV,
    Role.LOAD,
    Role.GRID_IMPORT,
    Role.GRID_EXPORT,
    Role.BATTERY_CHARGE,
    Role.BATTERY_DISCHARGE,
)


def _entity_selector() -> selector.EntitySelector:
    """Constrain the picker so it does half the validation itself."""
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            domain="sensor",
            device_class=["power", "energy"],
        )
    )


def _duplicate_entity(channels: dict[str, str]) -> str | None:
    """The entity mapped to more than one role, if any.

    One sensor in two roles measures one thing twice: as production and as
    consumption, say, the outlook would learn a house that uses exactly what it
    makes.
    """
    seen: dict[str, str] = {}
    for role_key, entity_id in channels.items():
        if entity_id in seen:
            return entity_id
        seen[entity_id] = role_key
    return None


def _channel_schema(discovery: Discovery, current: dict[str, str]) -> vol.Schema:
    fields: dict[Any, Any] = {}
    for role in MAPPED_ROLES:
        default = current.get(role.key) or discovery.suggestion(role)
        key = vol.Optional(role.key, description={"suggested_value": default})
        fields[key] = _entity_selector()

    # State of charge, last and optional, but *here* rather than only in the
    # options. It shipped in the options alone and the first person to use it
    # went looking in the wizard, beside the two battery channels, which is
    # where somebody mapping a battery obviously expects to be asked about it.
    # Being reachable only after setup made it something you had to already know
    # existed.
    #
    # Not a channel and never in `CONF_CHANNELS`: it is a level in percent
    # rather than a flow in watt-hours. It is where the battery outlook starts
    # from, and picking it is what makes the
    # next step ask how big the battery is. Stored in options, which is also
    # where reconfigure keeps it.
    fields[
        vol.Optional(OPT_BATTERY_SOC, description={"suggested_value": current.get(OPT_BATTERY_SOC)})
    ] = selector.EntitySelector(
        selector.EntitySelectorConfig(domain="sensor", device_class="battery")
    )
    return vol.Schema(fields)


#: The three battery settings, in the order they are asked.
BATTERY_OPTIONS: tuple[str, ...] = (
    OPT_BATTERY_CAPACITY_KWH,
    OPT_BATTERY_RESERVE_PCT,
    OPT_BATTERY_TARGET_PCT,
)


def _capacity_selector() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0.1,
            max=1000,
            step=0.1,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="kWh",
        )
    )


def _percent_selector(low: int, high: int) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=low,
            max=high,
            step=1,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="%",
        )
    )


def _battery_fields(current: dict[str, Any]) -> dict[Any, Any]:
    """Capacity, reserve and target, pre-filled from ``current``.

    Asked only once a charge-level sensor has been picked: they are what turns
    its percentage into hours, and without it there is nothing to turn.
    Capacity has no default because a guessed one would project somebody else's
    battery; reserve and target do, because the common values are right for most.
    """
    return {
        vol.Optional(
            OPT_BATTERY_CAPACITY_KWH,
            description={"suggested_value": current.get(OPT_BATTERY_CAPACITY_KWH)},
        ): _capacity_selector(),
        vol.Optional(
            OPT_BATTERY_RESERVE_PCT,
            default=current.get(OPT_BATTERY_RESERVE_PCT, DEFAULT_RESERVE_PCT),
        ): _percent_selector(0, 95),
        vol.Optional(
            OPT_BATTERY_TARGET_PCT,
            default=current.get(OPT_BATTERY_TARGET_PCT, DEFAULT_TARGET_PCT),
        ): _percent_selector(10, 100),
    }


def _battery_values(user_input: dict[str, Any]) -> dict[str, float]:
    """The battery settings that were actually given."""
    return {
        key: float(user_input[key])
        for key in BATTERY_OPTIONS
        if user_input.get(key) not in (None, "")
    }


def _battery_errors(user_input: dict[str, Any] | None, *, asked: bool) -> dict[str, str]:
    """A target at or below the reserve leaves the battery no range to use."""
    if user_input is None or not asked:
        return {}
    values = _battery_values(user_input)
    reserve = values.get(OPT_BATTERY_RESERVE_PCT, DEFAULT_RESERVE_PCT)
    target = values.get(OPT_BATTERY_TARGET_PCT, DEFAULT_TARGET_PCT)
    if target <= reserve:
        return {"base": "target_below_reserve"}
    return {}


def _topology_schema(
    channels: dict[str, str],
    providers: list[tuple[str, str]],
    current: dict[str, Any],
    soc_selected: bool = False,
) -> vol.Schema:
    """The forecast providers and the battery, whichever there is to ask about.

    Shared by setup and reconfigure so the two cannot drift. Empty when there
    is neither a provider to pick nor a charge-level sensor to size, and then
    the step is not shown at all: a page with nothing on it but a button is a
    question with no question in it.
    """
    fields: dict[Any, Any] = {}

    # ConfigEntrySelector takes a single entry and has no `multiple` option, so
    # the providers are listed by name instead — which is better anyway, since
    # the user recognises "Forecast.Solar" and not a UUID. The field is omitted
    # entirely when there is nothing to pick.
    if providers:
        fields[
            vol.Optional(
                CONF_FORECAST_ENTRIES, default=list(current.get(CONF_FORECAST_ENTRIES) or [])
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value=entry_id, label=label)
                    for entry_id, label in providers
                ],
                multiple=True,
                mode=selector.SelectSelectorMode.LIST,
            )
        )

    if soc_selected:
        fields.update(_battery_fields(current))

    return vol.Schema(fields)


def _topology_values(user_input: dict[str, Any]) -> dict[str, Any]:
    """What the step stores in ``entry.data``: the chosen forecast providers.

    Entries made before 0.30.0 also hold three answers about the house the
    energy-balance check used to ask. Nothing reads them now, and they are left
    where they are: removing them would make a rollback to 0.29 lose them.
    """
    return {CONF_FORECAST_ENTRIES: user_input.get(CONF_FORECAST_ENTRIES, [])}


def _channel_records(
    channels: dict[str, str], previous: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Channel records, keeping the origin of anything left alone.

    Origin is recorded because a mapping nobody confirmed is weaker evidence
    than one somebody chose, and diagnostics report it per channel. Stamping
    every channel "user" on the way through a reconfigure would silently promote
    every channel the user never looked at.
    """
    was = {
        record[CONF_ROLE]: (record[CONF_ENTITY_ID], record.get(CONF_ORIGIN, "user"))
        for record in previous
    }
    records: list[dict[str, str]] = []
    for role_key, entity_id in channels.items():
        before = was.get(role_key)
        origin = before[1] if before is not None and before[0] == entity_id else "user"
        records.append({CONF_ROLE: role_key, CONF_ENTITY_ID: entity_id, CONF_ORIGIN: origin})
    return records


class SolarSanityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Discover, review, confirm."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery = Discovery()
        self._channels: dict[str, str] = {}
        self._suggested: dict[str, str] = {}
        self._overlap: Overlap | None = None
        self._battery_soc: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Map channels, pre-filled from the Energy Dashboard where possible."""
        if not self._discovery.by_role:
            self._discovery = await async_discover(self.hass)
            self._suggested = {role.key: self._discovery.suggestion(role) for role in MAPPED_ROLES}

        errors: dict[str, str] = {}

        if user_input is not None:
            self._channels = {
                key: value for key, value in user_input.items() if value and key != OPT_BATTERY_SOC
            }
            self._battery_soc = user_input.get(OPT_BATTERY_SOC) or None
            duplicate = _duplicate_entity(self._channels)
            if Role.LOAD.key not in self._channels:
                # The outlook learns the house from its consumption; without it
                # there is nothing to project, so this is worth blocking on.
                errors["base"] = "load_required"
            elif Role.PV.key not in self._channels:
                errors["base"] = "pv_required"
            elif duplicate:
                errors["base"] = "duplicate_entity"
            else:
                self._overlap = find_overlap(
                    self.hass.config_entries.async_entries(DOMAIN), self._channels
                )
                if self._overlap is not None and self._overlap.decisive:
                    errors["base"] = "load_already_monitored"
                elif self._overlap is not None:
                    return await self.async_step_overlap()
                else:
                    return await self.async_step_topology()

        return self.async_show_form(
            step_id="user",
            data_schema=_channel_schema(self._discovery, self._channels),
            errors=errors,
            description_placeholders={
                "found": str(len([v for v in self._suggested.values() if v]))
            },
        )

    async def async_step_overlap(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name the sensor two installations would share, and let it be a choice.

        Not an abort. ``already_configured`` is terminal and leaves the user
        with nowhere to go, which is the position that produced the duplicate in
        the first place.
        """
        if user_input is not None:
            return await self.async_step_topology()

        overlap = self._overlap
        return self.async_show_form(
            step_id="overlap",
            data_schema=vol.Schema({}),
            description_placeholders={
                "other": overlap.title if overlap else "",
                "entity_id": overlap.entity_id if overlap else "",
            },
        )

    async def async_step_topology(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The forecast providers and the battery's size, when there are any."""
        providers = await async_forecast_providers(self.hass)
        schema = _topology_schema(
            self._channels, providers, dict(user_input or {}), bool(self._battery_soc)
        )
        if user_input is None and not schema.schema:
            user_input = {}
        errors = _battery_errors(user_input, asked=bool(self._battery_soc))
        if user_input is not None and not errors:
            return self.async_create_entry(
                title="Solar Sanity",
                data={
                    CONF_CHANNELS: [
                        {
                            CONF_ROLE: role_key,
                            CONF_ENTITY_ID: entity_id,
                            CONF_ORIGIN: (
                                "autodetected"
                                if self._suggested.get(role_key) == entity_id
                                else "user"
                            ),
                        }
                        for role_key, entity_id in self._channels.items()
                    ],
                    **_topology_values(user_input),
                },
                options=(
                    # Options, not data: none of these is a channel, and
                    # reconfigure must be able to change them without rewriting
                    # the mapping.
                    {OPT_BATTERY_SOC: self._battery_soc, **_battery_values(user_input)}
                    if self._battery_soc
                    else {}
                ),
            )

        return self.async_show_form(step_id="topology", data_schema=schema, errors=errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the mapping without losing history."""
        entry = self._get_reconfigure_entry()

        if not self._discovery.by_role:
            self._discovery = await async_discover(self.hass)

        current = {
            channel[CONF_ROLE]: channel[CONF_ENTITY_ID]
            for channel in entry.data.get(CONF_CHANNELS, [])
        }
        # So the field arrives pre-filled rather than looking unset.
        if soc := entry.options.get(OPT_BATTERY_SOC):
            current[OPT_BATTERY_SOC] = soc

        if user_input is not None:
            # Kept out of the channels: it is not a role, and letting it through
            # would write `battery_soc_entity` into CONF_CHANNELS as though it
            # were one, where `_channel_records` would then look up a Role that
            # does not exist.
            channels = {
                key: value for key, value in user_input.items() if value and key != OPT_BATTERY_SOC
            }
            self._battery_soc = user_input.get(OPT_BATTERY_SOC) or None
            error = None
            if Role.LOAD.key not in channels:
                error = "load_required"
            elif Role.PV.key not in channels:
                error = "pv_required"
            elif _duplicate_entity(channels):
                error = "duplicate_entity"
            else:
                clash = find_overlap(
                    self.hass.config_entries.async_entries(DOMAIN),
                    channels,
                    ignore_entry_id=entry.entry_id,
                )
                if clash is not None and clash.decisive:
                    error = "load_already_monitored"
            if error:
                # Re-render from what the user just submitted, not from the
                # stored config — otherwise their edits vanish on any error.
                return self.async_show_form(
                    step_id="reconfigure",
                    data_schema=_channel_schema(self._discovery, channels),
                    errors={"base": error},
                )
            self._channels = channels
            return await self.async_step_reconfigure_topology()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_channel_schema(self._discovery, current),
        )

    async def async_step_reconfigure_topology(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The same step setup shows, answerable again.

        Everything here was once write-once. A user who added a forecast
        provider after setup had no way to say so, and the only route that
        appeared to work was adding a second entry, which is how two
        installations end up fighting over one forecast archive.
        """
        entry = self._get_reconfigure_entry()
        providers = await async_forecast_providers(self.hass)
        current = {**entry.data, **entry.options, **(user_input or {})}
        schema = _topology_schema(self._channels, providers, current, bool(self._battery_soc))
        if user_input is None and not schema.schema:
            user_input = {}
        errors = _battery_errors(user_input, asked=bool(self._battery_soc))

        if user_input is not None and not errors:
            # `options` REPLACES rather than merges, and there is no
            # `options_updates` — checked against the 2026.2.3 source after
            # writing one that does not exist. Every local test still passed,
            # because the integration suite needs Home Assistant and does not
            # run here. So the merge happens explicitly, and clearing the field
            # genuinely clears it rather than being quietly ignored.
            options = {k: v for k, v in entry.options.items() if k != OPT_BATTERY_SOC}
            if self._battery_soc:
                options[OPT_BATTERY_SOC] = self._battery_soc
                # Only the rows this form showed are rewritten, and it shows them
                # only with a charge-level sensor. Clearing that sensor leaves the
                # battery's size where it was rather than forgetting it.
                for key in BATTERY_OPTIONS:
                    options.pop(key, None)
                options.update(_battery_values(user_input))

            return self.async_update_reload_and_abort(
                entry,
                data_updates={
                    CONF_CHANNELS: _channel_records(
                        self._channels, entry.data.get(CONF_CHANNELS, [])
                    ),
                    **_topology_values(user_input),
                },
                options=options,
            )

        return self.async_show_form(
            step_id="reconfigure_topology", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> OptionsFlow:
        return SolarSanityOptionsFlow()


class SolarSanityOptionsFlow(OptionsFlowWithReload):
    """Options. No ``__init__``, and ``config_entry`` is read-only now."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        fields = self._fields()
        errors = _battery_errors(user_input, asked=True)
        if (
            user_input is not None
            and len(set(user_input.get(OPT_LOAD_PROFILE_ALTERNATE_DAYS, []))) == 7
        ):
            errors[OPT_LOAD_PROFILE_ALTERNATE_DAYS] = "all_schedule_days"

        if user_input is not None and not errors:
            # Merge, then drop what was offered and left blank. `vol.Optional`
            # omits a cleared field rather than sending an empty one, so a plain
            # `{**options, **user_input}` puts the old value straight back and a
            # figure typed by mistake could never be removed — the same trap
            # reconfigure escapes by dropping the key first.
            #
            # Only keys this form actually rendered are touched. Anything else
            # in the options was written by another release, and a rollback to
            # it should find it where it was; a naive `data=user_input` would
            # erase it.
            options = dict(self.config_entry.options)
            for marker in fields:
                name = marker.schema
                if user_input.get(name) in (None, "", []):
                    options.pop(name, None)
                else:
                    options[name] = user_input[name]
            return self.async_create_entry(data=options)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(fields), {**self.config_entry.options, **(user_input or {})}
            ),
            errors=errors,
        )

    def _fields(self) -> dict[Any, Any]:
        """The rows this form offers, which is also the set it may clear."""
        fields: dict[Any, Any] = {}

        # Optional, and the start of the battery outlook: everything it projects
        # begins from this reading. Filtered to battery-percentage sensors,
        # which is what a BMS publishes.
        fields[vol.Optional(OPT_BATTERY_SOC)] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="battery")
        )
        # Always offered here, unlike in the wizard: this form cannot show a row
        # in answer to another row on the same page, and somebody adding the
        # charge-level sensor now needs to say how big the battery is now.
        # Suggested values rather than defaults, so clearing a row is possible
        # and falls back to the default instead of being refilled.
        fields[vol.Optional(OPT_BATTERY_CAPACITY_KWH)] = _capacity_selector()
        fields[vol.Optional(OPT_BATTERY_RESERVE_PCT)] = _percent_selector(0, 95)
        fields[vol.Optional(OPT_BATTERY_TARGET_PCT)] = _percent_selector(10, 100)

        # For the battery card's live flow only. The battery's mapped channels
        # are often lifetime energy counters, as on the reference installation,
        # and a counter cannot say what the battery is doing this second. The
        # sign has a default rather than a suggested value: an unticked box
        # saves as "no", so a form opened to change the capacity must not
        # quietly flip the battery's direction.
        fields[vol.Optional(OPT_BATTERY_POWER)] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="power")
        )
        fields[vol.Optional(OPT_BATTERY_POWER_CHARGING_POSITIVE, default=True)] = (
            selector.BooleanSelector()
        )
        fields[vol.Optional(OPT_LOAD_PROFILE_ALTERNATE_DAYS, default=[])] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value=day, label=label)
                    for day, label in zip(
                        WEEKDAYS,
                        (
                            "Monday",
                            "Tuesday",
                            "Wednesday",
                            "Thursday",
                            "Friday",
                            "Saturday",
                            "Sunday",
                        ),
                        strict=True,
                    )
                ],
                multiple=True,
                mode=selector.SelectSelectorMode.LIST,
            )
        )

        return fields
