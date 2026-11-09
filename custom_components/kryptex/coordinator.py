"""Polls the bridge's /status endpoint."""

from __future__ import annotations

import logging
from statistics import median
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import KryptexAuthError, KryptexBridgeClient, KryptexError
from .const import CONF_CURRENCY, CONF_POWER_ENTITY, DOMAIN, NO_CURRENCY, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

type KryptexConfigEntry = ConfigEntry[KryptexCoordinator]

# Mining power is the median of this many samples, one per poll, taken only while Kryptex mines.
POWER_SAMPLES = 60
STORAGE_VERSION = 1


def storage(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")


class KryptexCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """When the PC is off the update fails and the entities become unavailable."""

    config_entry: KryptexConfigEntry

    def __init__(self, hass: HomeAssistant, entry: KryptexConfigEntry, client: KryptexBridgeClient) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.client = client
        self._store = storage(hass, entry.entry_id)
        self._power_samples: list[float] = []
        self._was_mining = False

    async def async_load(self) -> None:
        """Mining power samples survive a restart, so the profitability works while Kryptex is paused."""
        stored = await self._store.async_load() or {}
        samples = stored.get("power_samples")
        if isinstance(samples, list):
            self._power_samples = [float(s) for s in samples if isinstance(s, (int, float)) and s > 0][-POWER_SAMPLES:]

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self.client.status()
        except KryptexAuthError as error:
            raise ConfigEntryAuthFailed(str(error)) from error
        except KryptexError as error:
            self._was_mining = False
            raise UpdateFailed(str(error)) from error
        mining = is_mining(data)
        # A sample is taken only when Kryptex mined at this poll and the previous one (60 s apart), so the power
        # of starting up or of a pause that just began is not counted.
        if mining and self._was_mining:
            self._sample_power()
        self._was_mining = mining
        return data

    def _sample_power(self) -> None:
        watts = self.current_power()
        if watts is None or watts <= 0:
            return
        self._power_samples = [*self._power_samples, watts][-POWER_SAMPLES:]
        self._store.async_delay_save(lambda: {"power_samples": self._power_samples}, 60)

    def current_power(self) -> float | None:
        """The power sensor chosen in the options, in watts."""
        entity_id = self.config_entry.options.get(CONF_POWER_ENTITY)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            value = float(state.state)
        except ValueError:
            return None
        return value * 1000 if state.attributes.get("unit_of_measurement") == UnitOfPower.KILO_WATT else value

    @property
    def mining_power(self) -> tuple[float, int] | None:
        """(median power in W while mining, number of samples), or None before the first samples."""
        if not self._power_samples:
            return None
        return median(self._power_samples), len(self._power_samples)

    @property
    def mining(self) -> bool | None:
        return is_mining(self.data) if self.data else None

    def section(self, name: str) -> dict[str, Any]:
        """A part of the answer, or {} when it is missing or its reading failed on the PC."""
        part = (self.data or {}).get(name) or {}
        return part if part.get("ok") is True else {}

    @property
    def devices(self) -> list[dict[str, Any]]:
        return self.section("service").get("devices") or []

    @property
    def currency(self) -> str | None:
        """Display currency chosen in the options (ISO code, upper case), or None."""
        value = self.config_entry.options.get(CONF_CURRENCY, NO_CURRENCY)
        return None if value == NO_CURRENCY else value.upper()

    def btc_price(self) -> float | None:
        """Price of one bitcoin in the display currency, from Kryptex's own rates."""
        if self.currency is None:
            return None
        return (self.section("rates").get("btc_price") or {}).get(self.currency.lower())


def is_mining(data: dict[str, Any]) -> bool:
    """The app runs, is not paused, and has a fresh income reading (hashrate above zero)."""
    service = data.get("service") or {}
    profitability = data.get("profitability") or {}
    return (
        data.get("running") is True
        and not (service.get("ok") is True and service.get("paused") is True)
        and profitability.get("ok") is True
    )
