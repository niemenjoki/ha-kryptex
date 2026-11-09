"""Config flow: address and API key of the bridge. Options: display currency and profitability."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import KryptexAuthError, KryptexBridgeClient, KryptexError
from .const import (
    CONF_CURRENCY,
    CONF_POWER_ENTITY,
    CONF_PRICE_ENTITY,
    CONF_PRICE_UNIT,
    DEFAULT_PORT,
    DOMAIN,
    NO_CURRENCY,
    PRICE_CENTS_PER_KWH,
    PRICE_PER_KWH,
)

# Used when the bridge has not answered yet; the bridge's own list (Kryptex's rate table) is used when available.
FALLBACK_CURRENCIES = ["usd", "eur", "gbp"]

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
        vol.Required(CONF_API_KEY): str,
    }
)

RECONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
        vol.Optional(CONF_API_KEY): str,
    }
)


class KryptexConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> KryptexOptionsFlow:
        return KryptexOptionsFlow()

    async def _validate(self, data: dict[str, Any]) -> str | None:
        client = KryptexBridgeClient(async_get_clientsession(self.hass), data[CONF_HOST], data[CONF_PORT], data[CONF_API_KEY])
        try:
            await client.health()
        except KryptexAuthError:
            return "invalid_auth"
        except KryptexError:
            return "cannot_connect"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()
            if (error := await self._validate(user_input)) is None:
                return self.async_create_entry(title="Kryptex", data=user_input)
            errors["base"] = error
        return self.async_show_form(
            step_id="user", data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input), errors=errors
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """New address or port of the bridge, e.g. after the PC got a new address. The API key may stay empty."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input[CONF_PORT],
                CONF_API_KEY: user_input.get(CONF_API_KEY) or entry.data[CONF_API_KEY],
            }
            unique_id = f"{data[CONF_HOST]}:{data[CONF_PORT]}"
            await self.async_set_unique_id(unique_id)
            if unique_id != entry.unique_id:
                self._abort_if_unique_id_configured()
            if (error := await self._validate(data)) is None:
                return self.async_update_reload_and_abort(entry, unique_id=unique_id, data=data)
            errors["base"] = error
        suggested = {CONF_HOST: entry.data[CONF_HOST], CONF_PORT: entry.data[CONF_PORT], **(user_input or {})}
        suggested.pop(CONF_API_KEY, None)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(RECONFIGURE_SCHEMA, suggested),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**entry.data, CONF_API_KEY: user_input[CONF_API_KEY]}
            if (error := await self._validate(data)) is None:
                return self.async_update_reload_and_abort(entry, data=data)
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}), errors=errors
        )


class KryptexOptionsFlow(OptionsFlow):
    """Display currency (default: Home Assistant's currency) and optional profitability sensors."""

    def _currencies(self) -> list[str]:
        coordinator = getattr(self.config_entry, "runtime_data", None)
        prices = coordinator.section("rates").get("btc_price") if coordinator else None
        return sorted(prices) if prices else FALLBACK_CURRENCIES

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            profitability = user_input.get(CONF_POWER_ENTITY) or user_input.get(CONF_PRICE_ENTITY)
            if profitability and user_input[CONF_CURRENCY] == NO_CURRENCY:
                errors["base"] = "currency_required"
            elif user_input.get(CONF_PRICE_ENTITY) and not user_input.get(CONF_POWER_ENTITY):
                errors["base"] = "power_required"
            else:
                return self.async_create_entry(data=user_input)

        currencies = self._currencies()
        home = (self.hass.config.currency or "").lower()
        default_currency = home if home in currencies else NO_CURRENCY
        schema = vol.Schema(
            {
                vol.Required(CONF_CURRENCY, default=default_currency): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=NO_CURRENCY, label="BTC only"),
                            *(selector.SelectOptionDict(value=c, label=c.upper()) for c in currencies),
                        ],
                        translation_key="currency",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_POWER_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.POWER)
                ),
                vol.Optional(CONF_PRICE_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Required(CONF_PRICE_UNIT, default=PRICE_PER_KWH): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=[PRICE_PER_KWH, PRICE_CENTS_PER_KWH], translation_key="price_unit")
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or self.config_entry.options),
            errors=errors,
        )
