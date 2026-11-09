"""Shows when a new Kryptex version is offered. Installing is left to the PC on purpose (see README)."""

from __future__ import annotations

from awesomeversion import AwesomeVersion, AwesomeVersionException

from homeassistant.components.update import UpdateEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import KryptexConfigEntry
from .entity import KryptexEntity

PARALLEL_UPDATES = 0

# The Kryptex installer asks for Windows administrator approval (UAC), and that prompt can only be answered at the PC.
RELEASE_SUMMARY = (
    "Install Kryptex updates at the PC: the installer asks for Windows administrator approval, "
    "which cannot be given remotely."
)


async def async_setup_entry(
    hass: HomeAssistant, entry: KryptexConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([KryptexUpdate(entry.runtime_data, "app_update")])


class KryptexUpdate(KryptexEntity, UpdateEntity):
    _attr_title = "Kryptex"
    _attr_release_summary = RELEASE_SUMMARY

    @property
    def _service(self) -> dict:
        return self.coordinator.section("service")

    @property
    def available(self) -> bool:
        return super().available and self._service.get("version") is not None

    @property
    def installed_version(self) -> str | None:
        return self._service.get("version")

    @property
    def latest_version(self) -> str | None:
        update = self._service.get("update") or {}
        if not update.get("available"):
            return self.installed_version
        return update.get("version") or "unknown"

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        if not (self._service.get("update") or {}).get("available"):
            return False
        try:
            return AwesomeVersion(latest_version) > AwesomeVersion(installed_version)
        except AwesomeVersionException:
            return latest_version != installed_version
