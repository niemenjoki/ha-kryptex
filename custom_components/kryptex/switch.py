"""Starts or stops the Kryptex app, and pauses or resumes mining with Kryptex's own pause."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import KryptexError
from .coordinator import KryptexConfigEntry
from .entity import KryptexEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: KryptexConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([KryptexMiningSwitch(coordinator, "mining"), KryptexPauseSwitch(coordinator, "paused")])


class KryptexSwitch(KryptexEntity, SwitchEntity):
    async def _run(self, command: Callable[[], Awaitable[Any]]) -> None:
        try:
            await command()
        except KryptexError as error:
            raise HomeAssistantError(f"Kryptex command failed: {error}") from error
        await self.coordinator.async_request_refresh()


class KryptexMiningSwitch(KryptexSwitch):
    """On = the Kryptex app is running. Off closes the app."""

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.get("running") is not None

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get("running")

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._run(self.coordinator.client.start)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._run(self.coordinator.client.stop)


class KryptexPauseSwitch(KryptexSwitch):
    """On = mining is paused in Kryptex. The app keeps running; the computer is not touched."""

    _attr_icon = "mdi:pause-circle"

    @property
    def available(self) -> bool:
        return super().available and isinstance(self.coordinator.section("service").get("paused"), bool)

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.section("service").get("paused")

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._run(self.coordinator.client.pause)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._run(self.coordinator.client.resume)
