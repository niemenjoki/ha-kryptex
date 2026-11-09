"""Kryptex mining income, balance, devices and control through the Kryptex bridge."""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntry

from .api import KryptexBridgeClient
from .const import DOMAIN
from .coordinator import KryptexConfigEntry, KryptexCoordinator, storage

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.SWITCH, Platform.UPDATE]


async def async_setup_entry(hass: HomeAssistant, entry: KryptexConfigEntry) -> bool:
    client = KryptexBridgeClient(
        async_get_clientsession(hass), entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_API_KEY]
    )
    coordinator = KryptexCoordinator(hass, entry, client)
    await coordinator.async_load()
    # The PC may be off at startup: do not block setup, the entities stay unavailable until it answers.
    await coordinator.async_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _options_updated(hass: HomeAssistant, entry: KryptexConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: KryptexConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: KryptexConfigEntry, device: DeviceEntry
) -> bool:
    """A mining device that Kryptex no longer lists can be deleted from the device page."""
    current = {f"{entry.entry_id}_{d['id']}" for d in entry.runtime_data.devices}
    return not any(domain == DOMAIN and (ident == entry.entry_id or ident in current) for domain, ident in device.identifiers)


async def async_remove_entry(hass: HomeAssistant, entry: KryptexConfigEntry) -> None:
    """Delete the stored mining power samples."""
    await storage(hass, entry.entry_id).async_remove()
