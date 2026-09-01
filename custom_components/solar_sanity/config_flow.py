"""Setup and reconfiguration.

Two things the predecessor could not do, both table stakes for a product whose
whole job is remapping sensors:

* **Reconfigure.** Everything chosen at setup lived in ``entry.data``, which the
  options flow never touched, so correcting a mis-mapped sensor meant deleting
  the entry and orphaning every entity's history. Reconfigure covers the
  topology answers and the forecast providers too — they were write-once, and a
  setting that can only be changed by starting again is how two installations
  end up fighting over one forecast archive.
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
    OPT_BATTERY_SOC,
    OPT_SUPPRESSED,
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


def _duplicate_entity(channels: dict[str, str]) -> str | None:
    """The entity mapped to more than one role, if any.

    One sensor on both sides of the identity cancels itself out and makes the
    balance meaningless — and the live tripwire would then report the same
    entity flowing two ways at once, naming it twice in a single sentence.
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
    return vol.Schema(fields)


def _battery_mapped(channels: dict[str, str]) -> bool:
    return Role.BATTERY_CHARGE.key in channels or Role.BATTERY_DISCHARGE.key in channels


def _both_grid_mapped(channels: dict[str, str]) -> bool:
    return Role.GRID_IMPORT.key in channels and Role.GRID_EXPORT.key in channels


def _topology_schema(
    channels: dict[str, str],
    providers: list[tuple[str, str]],
    current: dict[str, Any],
) -> vol.Schema:
    """The questions worth asking, given what the mapping already answers.

    Shared by setup and reconfigure so the two cannot drift. Asking "do you have
    a battery?" of someone who just mapped two battery sensors is the kind of
    question that makes software feel stupid, and it would be worse the second
    time.
    """
    fields: dict[Any, Any] = {}

    if not _battery_mapped(channels):
        fields[vol.Required(CONF_HAS_BATTERY, default=current.get(CONF_HAS_BATTERY, "unknown"))] = (
            _TRISTATE
        )

    # Only ambiguous when import is mapped alone. Both mapped means two
    # dedicated sensors; neither mapped means there is nothing to interpret.
    import_only = Role.GRID_IMPORT.key in channels and Role.GRID_EXPORT.key not in channels
    if import_only:
        fields[vol.Required(CONF_GRID_IS_NET, default=current.get(CONF_GRID_IS_NET, "unknown"))] = (
            _TRISTATE
        )

    # Not inferable from the mapping at all — a backup-panel sensor looks exactly
    # like a whole-house one until the residual says otherwise.
    fields[
        vol.Required(CONF_LOAD_WHOLE_HOUSE, default=current.get(CONF_LOAD_WHOLE_HOUSE, "unknown"))
    ] = _TRISTATE

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

    return vol.Schema(fields)


def _topology_values(user_input: dict[str, Any], channels: dict[str, str]) -> dict[str, Any]:
    """The answers to store, filling in what the mapping settles outright."""
    return {
        # A mapped battery answers the question outright; likewise two dedicated
        # grid sensors mean it is not a net meter.
        CONF_HAS_BATTERY: user_input.get(
            CONF_HAS_BATTERY, "yes" if _battery_mapped(channels) else "unknown"
        ),
        CONF_GRID_IS_NET: user_input.get(
            CONF_GRID_IS_NET, "no" if _both_grid_mapped(channels) else "unknown"
        ),
        CONF_LOAD_WHOLE_HOUSE: user_input.get(CONF_LOAD_WHOLE_HOUSE, "unknown"),
        CONF_FORECAST_ENTRIES: user_input.get(CONF_FORECAST_ENTRIES, []),
    }


def _channel_records(
    channels: dict[str, str], previous: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Channel records, keeping the origin of anything left alone.

    Origin is not cosmetic: an autodetected channel has its findings downgraded
    one confidence step, because a mapping nobody confirmed is weaker evidence
    than one somebody chose. Stamping every channel "user" on the way through a
    reconfigure would silently promote every channel the user never looked at,
    and a run through the form without changing anything would quietly raise the
    confidence of the whole installation.
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

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Map channels, pre-filled from the Energy Dashboard where possible."""
        if not self._discovery.by_role:
            self._discovery = await async_discover(self.hass)
            self._suggested = {role.key: self._discovery.suggestion(role) for role in MAPPED_ROLES}

        errors: dict[str, str] = {}

        if user_input is not None:
            self._channels = {key: value for key, value in user_input.items() if value}
            duplicate = _duplicate_entity(self._channels)
            if Role.LOAD.key not in self._channels:
                # Without consumption the identity closes by definition and the
                # whole check is vacuous, so this is worth blocking on.
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
        """Three questions the user knows the answer to."""
        if user_input is not None:
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
                    **_topology_values(user_input, self._channels),
                },
            )

        providers = await async_forecast_providers(self.hass)
        return self.async_show_form(
            step_id="topology",
            data_schema=_topology_schema(self._channels, providers, {}),
        )

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
        """The same questions setup asks, answerable again.

        Everything here was previously write-once. A user who added a forecast
        provider after setup, or realised their consumption sensor covers only
        the backup panel, had no way to say so — and the only route that
        appeared to work was adding a second entry, which is how two
        installations end up fighting over one forecast archive.
        """
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            return self.async_update_reload_and_abort(
                entry,
                data_updates={
                    CONF_CHANNELS: _channel_records(
                        self._channels, entry.data.get(CONF_CHANNELS, [])
                    ),
                    **_topology_values(user_input, self._channels),
                },
            )

        providers = await async_forecast_providers(self.hass)
        return self.async_show_form(
            step_id="reconfigure_topology",
            data_schema=_topology_schema(self._channels, providers, dict(entry.data)),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> OptionsFlow:
        return SolarSanityOptionsFlow()


def _readable(code: str) -> str:
    """A fault identifier, as a sentence rather than as a symbol.

    These are stable identifiers that appear in entity attributes and events, so
    they cannot be renamed to read well. Turning them over here costs nothing and
    means a settings page does not ask somebody to make a decision about
    ``signed_net_in_dedicated_slot``.
    """
    return code.replace("_", " ").capitalize()


class SolarSanityOptionsFlow(OptionsFlowWithReload):
    """Options. No ``__init__``, and ``config_entry`` is read-only now."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={**self.config_entry.options, **user_input})

        fields: dict[Any, Any] = {
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

        # Optional, and offered here rather than at setup because it buys one
        # narrow thing rather than being part of the balance: when a battery
        # meter's reported throughput steps, this is what says whether the
        # battery changed or the meter did. Filtered to battery-percentage
        # sensors, which is what a BMS publishes.
        fields[vol.Optional(OPT_BATTERY_SOC)] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="battery")
        )

        dismissible = self._dismissible_codes()
        if dismissible:
            fields[vol.Optional(OPT_SUPPRESSED)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=code, label=_readable(code))
                        for code in dismissible
                    ],
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(fields), self.config_entry.options
            ),
        )

    def _dismissible_codes(self) -> list[str]:
        """Findings this installation has actually seen, plus anything already off.

        The engine has honoured ``suppressed_codes`` in five places for a long
        time and nothing could ever write it — a setting the code kept faith with
        and the product never offered. This is the missing half.

        Deliberately not every code the engine knows. A list of thirty
        identifiers is a list nobody reads, and most of them describe faults this
        house does not have; offering somebody the chance to dismiss a diagnosis
        they have never been given is how a settings page teaches people to
        ignore it. What is offered is what has been said: the current finding,
        the ones ranked behind it, and whatever is already dismissed — the last
        of those because a setting you cannot see is a setting you cannot undo.
        """
        entry: Any = self.config_entry
        seen: set[str] = set(entry.options.get(OPT_SUPPRESSED, []))

        runtime = getattr(entry, "runtime_data", None)
        report = getattr(getattr(runtime, "coordinator", None), "report", None)
        if report is not None:
            if report.finding is not None:
                seen.add(report.finding.code)
            seen.update(report.deferred)

        return sorted(seen)
