"""Base entities: the Kryptex app itself, and each mining device (CPU or GPU) as its own device."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import KryptexCoordinator


class KryptexEntity(CoordinatorEntity[KryptexCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: KryptexCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = kryptex_device_info(coordinator)


VENDORS = {"amd": "AMD", "nvidia": "NVIDIA", "intel": "Intel"}
# Home Assistant 2026.8 and newer link a child device by registry id; via_device is removed in 2027.8.
HAS_VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__annotations__


def kryptex_device_info(coordinator: KryptexCoordinator) -> DeviceInfo:
    """The Kryptex device itself (one per bridge)."""
    entry = coordinator.config_entry
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Kryptex",
        model="Kryptex bridge",
        sw_version=(coordinator.data or {}).get("version"),
    )


def link_to_kryptex(coordinator: KryptexCoordinator) -> dict:
    """DeviceInfo fields that make a mining device a child of the Kryptex device."""
    entry = coordinator.config_entry
    if not HAS_VIA_DEVICE_ID:
        return {"via_device": (DOMAIN, entry.entry_id)}
    hub = dr.async_get(coordinator.hass).async_get_or_create(
        config_entry_id=entry.entry_id, **kryptex_device_info(coordinator)
    )
    return {"via_device_id": hub.id}


class KryptexDeviceEntity(CoordinatorEntity[KryptexCoordinator]):
    """An entity of one mining device. Unavailable while Kryptex does not list the device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: KryptexCoordinator, device: dict[str, Any], key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self.device_id = device["id"]
        self._attr_unique_id = f"{entry.entry_id}_{self.device_id}_{key}"
        self._attr_translation_key = f"device_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{self.device_id}")},
            name=device["name"],
            manufacturer=VENDORS.get(str(device.get("vendor")).lower(), device.get("vendor")),
            model=device["name"],
            hw_version=device.get("architecture"),
            **link_to_kryptex(coordinator),
        )

    @property
    def device(self) -> dict[str, Any] | None:
        return next((d for d in self.coordinator.devices if d["id"] == self.device_id), None)

    @property
    def available(self) -> bool:
        return super().available and self.device is not None
