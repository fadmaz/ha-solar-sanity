"""Setup and reconfiguration.

Two things the predecessor could not do, both table stakes for a product whose
whole job is remapping sensors:

* **Reconfigure.** Everything chosen at setup lived in ``entry.data``, which the
  options flow never touched, so correcting a mis-mapped sensor meant deleting
  the entry and orphaning every entity's history.
* **A unique id**, so a second entry for the same installation is caught.

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

from .analysis.model import Role
from .const import (
    CONF_CHANNELS,
    CONF_ENTITY_ID,
    CONF_FORECAST_ENTRIES,
    CONF_GRID_IS_NET,
    CONF_GUARANTEED_ANNUAL_KWH,
    CONF_HAS_BATTERY,
    CONF_LOAD_WHOLE_HOUSE,
    CONF_ORIGIN,
    CONF_ROLE,
    DOMAIN,
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

#: Every question offers "not sure", which defers to inference rather than
#: forcing a guess that the engine would then treat as fact.
_TRISTATE = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=["yes", "no", "unknown"],
        translation_key="tristate",
        mode=selector.SelectSelectorMode.DROPDOWN,
    )
)


def _entity_selector() -> selector.EntitySelector:
    """Constrain the picker so it does half the validation itself."""
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            domain="sensor",
            device_class=["power", "energy"],
        )
    )


def _channel_schema(discovery: Discovery, current: dict[str, str]) -> vol.Schema:
    fields: dict[Any, Any] = {}
    for role in MAPPED_ROLES:
        default = current.get(role.key) or discovery.suggestion(role)
        key = vol.Optional(role.key, description={"suggested_value": default})
        fields[key] = _entity_selector()
    return vol.Schema(fields)


class SolarSanityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Discover, review, confirm."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery = Discovery()
        self._channels: dict[str, str] = {}
        self._suggested: dict[str, str] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Map channels, pre-filled from the Energy Dashboard where possible."""
        if not self._discovery.by_role:
            self._discovery = await async_discover(self.hass)
            self._suggested = {role.key: self._discovery.suggestion(role) for role in MAPPED_ROLES}

        errors: dict[str, str] = {}

        if user_input is not None:
            self._channels = {key: value for key, value in user_input.items() if value}
            if Role.LOAD.key not in self._channels:
                # Without consumption the identity closes by definition and the
                # whole check is vacuous, so this is worth blocking on.
                errors["base"] = "load_required"
            elif Role.PV.key not in self._channels:
                errors["base"] = "pv_required"
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

    async def async_step_topology(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Three questions the user knows the answer to."""
        if user_input is not None:
            await self.async_set_unique_id(self._unique_id())
            self._abort_if_unique_id_configured()
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
                    # A mapped battery answers the question outright; likewise
                    # two dedicated grid sensors mean it is not a net meter.
                    CONF_HAS_BATTERY: user_input.get(
                        CONF_HAS_BATTERY, "yes" if self._battery_mapped else "unknown"
                    ),
                    CONF_GRID_IS_NET: user_input.get(
                        CONF_GRID_IS_NET, "no" if self._both_grid_mapped else "unknown"
                    ),
                    CONF_LOAD_WHOLE_HOUSE: user_input.get(CONF_LOAD_WHOLE_HOUSE, "unknown"),
                    CONF_FORECAST_ENTRIES: user_input.get(CONF_FORECAST_ENTRIES, []),
                },
            )

        # Ask only what the user actually knows and we cannot work out. Asking
        # "do you have a battery?" of someone who just mapped two battery
        # sensors is the kind of question that makes software feel stupid.
        fields: dict[Any, Any] = {}

        battery_mapped = (
            Role.BATTERY_CHARGE.key in self._channels
            or Role.BATTERY_DISCHARGE.key in self._channels
        )
        if not battery_mapped:
            fields[vol.Required(CONF_HAS_BATTERY, default="unknown")] = _TRISTATE

        # Only ambiguous when import is mapped alone. Both mapped means two
        # dedicated sensors; neither mapped means there is nothing to interpret.
        import_only = (
            Role.GRID_IMPORT.key in self._channels and Role.GRID_EXPORT.key not in self._channels
        )
        if import_only:
            fields[vol.Required(CONF_GRID_IS_NET, default="unknown")] = _TRISTATE

        # Not inferable from the mapping at all — a backup-panel sensor looks
        # exactly like a whole-house one until the residual says otherwise.
        fields[vol.Required(CONF_LOAD_WHOLE_HOUSE, default="unknown")] = _TRISTATE

        # ConfigEntrySelector takes a single entry and has no `multiple` option,
        # so the providers are listed by name instead — which is better anyway,
        # since the user recognises "Forecast.Solar" and not a UUID. The field is
        # omitted entirely when there is nothing to pick.
        providers = await async_forecast_providers(self.hass)
        if providers:
            fields[vol.Optional(CONF_FORECAST_ENTRIES, default=[])] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=entry_id, label=label)
                        for entry_id, label in providers
                    ],
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )

        return self.async_show_form(step_id="topology", data_schema=vol.Schema(fields))

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

        if user_input is not None:
            channels = {key: value for key, value in user_input.items() if value}
            if Role.LOAD.key not in channels:
                return self.async_show_form(
                    step_id="reconfigure",
                    data_schema=_channel_schema(self._discovery, current),
                    errors={"base": "load_required"},
                )
            return self.async_update_reload_and_abort(
                entry,
                data_updates={
                    CONF_CHANNELS: [
                        {CONF_ROLE: role_key, CONF_ENTITY_ID: entity_id, CONF_ORIGIN: "user"}
                        for role_key, entity_id in channels.items()
                    ]
                },
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_channel_schema(self._discovery, current),
        )

    @property
    def _battery_mapped(self) -> bool:
        return (
            Role.BATTERY_CHARGE.key in self._channels
            or Role.BATTERY_DISCHARGE.key in self._channels
        )

    @property
    def _both_grid_mapped(self) -> bool:
        return Role.GRID_IMPORT.key in self._channels and Role.GRID_EXPORT.key in self._channels

    def _unique_id(self) -> str:
        """Identify an installation by the channels it monitors."""
        return "|".join(sorted(self._channels.values()))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> OptionsFlow:
        return SolarSanityOptionsFlow()


class SolarSanityOptionsFlow(OptionsFlowWithReload):
    """Options. No ``__init__``, and ``config_entry`` is read-only now."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={**self.config_entry.options, **user_input})

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
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
                ),
                self.config_entry.options,
            ),
        )
