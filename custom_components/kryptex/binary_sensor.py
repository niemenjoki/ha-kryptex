"""Whether the Kryptex app is running, and whether it is mining."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import KryptexConfigEntry
from .entity import KryptexEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: KryptexConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([KryptexRunningSensor(coordinator, "running"), KryptexMiningSensor(coordinator, "mining")])


class KryptexRunningSensor(KryptexEntity, BinarySensorEntity):
    """The Kryptex app (Kryptex.exe) is running. Stays on while Kryptex is paused."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.get("running") is not None

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get("running")


class KryptexMiningSensor(KryptexEntity, BinarySensorEntity):
    """Kryptex is mining: the app runs, it is not paused, and it reports a hashrate."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.get("running") is not None

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.mining
