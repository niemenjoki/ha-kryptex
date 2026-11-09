"""Sensors: balance and income in bitcoin, optional display currency and profitability, and mining devices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    PERCENTAGE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_POWER_ENTITY, CONF_PRICE_ENTITY, CONF_PRICE_UNIT, PRICE_CENTS_PER_KWH, PRICE_PER_KWH
from .coordinator import KryptexConfigEntry, KryptexCoordinator
from .entity import KryptexDeviceEntity, KryptexEntity

PARALLEL_UPDATES = 0
BTC = "BTC"


# --- Account: balance and income in bitcoin ---------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class KryptexSensorDescription(SensorEntityDescription):
    section: str
    field: str
    attributes: Callable[[dict[str, Any]], dict[str, Any]] = lambda _: {}


BTC_SENSORS = (
    KryptexSensorDescription(
        key="balance", section="balance", field="total_btc",
        native_unit_of_measurement=BTC, state_class=SensorStateClass.TOTAL, suggested_display_precision=8,
        attributes=lambda s: {"updated_at": s.get("updated_at")},
    ),
    KryptexSensorDescription(
        key="withdrawable_balance", section="balance", field="withdrawable_btc",
        native_unit_of_measurement=BTC, state_class=SensorStateClass.TOTAL, suggested_display_precision=8,
    ),
    KryptexSensorDescription(
        key="daily_income", section="profitability", field="daily_btc",
        native_unit_of_measurement=BTC, state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=8,
        attributes=lambda s: {"measured_at": s.get("measured_at"), "devices": s.get("devices")},
    ),
    KryptexSensorDescription(
        key="monthly_income", section="profitability", field="monthly_btc",
        native_unit_of_measurement=BTC, state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=8,
    ),
)


class KryptexSensor(KryptexEntity, SensorEntity):
    """A value in bitcoin. A failed read on the PC (e.g. not mining) makes only the sensors of that reading unavailable."""

    entity_description: KryptexSensorDescription

    def __init__(self, coordinator: KryptexCoordinator, description: KryptexSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def btc_value(self) -> float | None:
        return self.coordinator.section(self.entity_description.section).get(self.entity_description.field)

    @property
    def available(self) -> bool:
        return super().available and self.btc_value is not None

    @property
    def native_value(self) -> float | None:
        return self.btc_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.entity_description.attributes(self.coordinator.section(self.entity_description.section))


class KryptexCurrencySensor(KryptexSensor):
    """The same value in the display currency, converted with Kryptex's own bitcoin rate."""

    def __init__(self, coordinator: KryptexCoordinator, description: KryptexSensorDescription) -> None:
        currency = coordinator.currency
        description = KryptexSensorDescription(
            key=f"{description.key}_currency", section=description.section, field=description.field,
            native_unit_of_measurement=currency, state_class=description.state_class, suggested_display_precision=2,
            device_class=SensorDeviceClass.MONETARY if description.state_class == SensorStateClass.TOTAL else None,
        )
        super().__init__(coordinator, description)
        self._attr_translation_placeholders = {"currency": currency}

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.btc_price() is not None

    @property
    def native_value(self) -> float | None:
        price = self.coordinator.btc_price()
        return None if self.btc_value is None or price is None else round(self.btc_value * price, 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {}


class KryptexBitcoinPriceSensor(KryptexEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: KryptexCoordinator) -> None:
        super().__init__(coordinator, "bitcoin_price")
        self._attr_native_unit_of_measurement = coordinator.currency

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.btc_price() is not None

    @property
    def native_value(self) -> float | None:
        return self.coordinator.btc_price()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"source": "Kryptex", "updated_at": self.coordinator.section("rates").get("updated_at")}


# --- Optional profitability -------------------------------------------------------------------------------------

def _float_state(hass: HomeAssistant, entity_id: str | None) -> tuple[float | None, str | None]:
    state = hass.states.get(entity_id) if entity_id else None
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None, None
    try:
        return float(state.state), state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    except ValueError:
        return None, None


class KryptexProfitSensor(KryptexEntity, SensorEntity):
    """Break-even electricity price, electricity cost and net profit per day while mining.

    They are shown also while Kryptex is paused or closed, so an automation can decide when to mine:
    - Income: Kryptex's estimate now while mining; otherwise the bridge's estimate (the last mining hashrates with
      Kryptex's newest coefficients).
    - Power: the median of the PC's measured power while mining (kept over restarts), not the idle power of a
      paused PC. Kryptex itself reports only GPU power, so it is not used here.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: KryptexCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        options = coordinator.config_entry.options
        self._power_entity = options.get(CONF_POWER_ENTITY)
        self._price_entity = options.get(CONF_PRICE_ENTITY)
        self._cents = options.get(CONF_PRICE_UNIT, PRICE_PER_KWH) == PRICE_CENTS_PER_KWH
        currency = coordinator.currency
        if key == "break_even_price":
            self._attr_suggested_display_precision = 2 if self._cents else 4
            self._attr_native_unit_of_measurement = "c/kWh" if self._cents else f"{currency}/kWh"
        else:
            self._attr_suggested_display_precision = 2
            self._attr_native_unit_of_measurement = currency

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        tracked = [e for e in (self._power_entity, self._price_entity) if e]
        self.async_on_remove(async_track_state_change_event(self.hass, tracked, self._source_changed))
        if self._price_entity and self.translation_key == "break_even_price":
            # Show the break-even price in the same unit as the user's price sensor, so they compare directly.
            _, unit = _float_state(self.hass, self._price_entity)
            if unit:
                self._attr_native_unit_of_measurement = unit

    @callback
    def _source_changed(self, event: Event[EventStateChangedData]) -> None:
        self.async_write_ha_state()

    def _income(self) -> tuple[float, str, str | None] | None:
        """(income BTC/day, basis, time of the hashrates used)."""
        now = self.coordinator.section("profitability")
        if now.get("daily_btc") is not None:
            return now["daily_btc"], "mining_now", now.get("measured_at")
        estimate = self.coordinator.section("estimate")
        if estimate.get("daily_btc") is not None:
            return estimate["daily_btc"], "estimate", estimate.get("hashrate_measured_at")
        return None

    def _power(self) -> tuple[float, str] | None:
        """(power while mining in W, basis)."""
        if (stored := self.coordinator.mining_power) is not None:
            return stored[0], f"median of {stored[1]} samples while mining"
        # No samples yet (first minutes after setup): the power now is right only while mining.
        if self.coordinator.mining and (watts := self.coordinator.current_power()) is not None and watts > 0:
            return watts, "power now"
        return None

    def _values(self) -> dict[str, Any] | None:
        income, power, price_btc = self._income(), self._power(), self.coordinator.btc_price()
        if income is None or power is None or price_btc is None:
            return None
        daily_btc, income_basis, hashrate_at = income
        watts, power_basis = power
        kwh_per_day = watts / 1000 * 24
        income_per_day = daily_btc * price_btc
        values: dict[str, Any] = {
            "income": income_per_day, "kwh_per_day": kwh_per_day, "income_basis": income_basis,
            "hashrate_measured_at": hashrate_at, "power_basis": power_basis,
        }
        if kwh_per_day > 0:
            values["break_even_price"] = income_per_day / kwh_per_day * (100 if self._cents else 1)
        price, _ = _float_state(self.hass, self._price_entity)
        if price is not None:
            cost = kwh_per_day * (price / 100 if self._cents else price)
            values["electricity_cost"] = cost
            values["net_profit"] = income_per_day - cost
        return values

    @property
    def available(self) -> bool:
        values = self._values()
        return super().available and values is not None and self.translation_key in values

    @property
    def native_value(self) -> float | None:
        values = self._values()
        return None if values is None or self.translation_key not in values else round(values[self.translation_key], 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        values = self._values() or {}
        return {
            "income_per_day": round(values["income"], 4) if "income" in values else None,
            "energy_per_day_kwh": round(values["kwh_per_day"], 3) if "kwh_per_day" in values else None,
            "income_basis": values.get("income_basis"),
            "hashrate_measured_at": values.get("hashrate_measured_at"),
            "power_basis": values.get("power_basis"),
        }


# --- Mining devices ---------------------------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class KryptexDeviceSensorDescription(SensorEntityDescription):
    field: str
    attributes: Callable[[dict[str, Any]], dict[str, Any]] = lambda _: {}


DEVICE_SENSORS = (
    KryptexDeviceSensorDescription(key="state", field="state"),
    KryptexDeviceSensorDescription(
        key="algorithm", field="algorithm",
        attributes=lambda d: {"coin": d.get("coin"), "algorithms": d.get("algorithms")},
    ),
    KryptexDeviceSensorDescription(
        key="miner", field="miner", entity_category=EntityCategory.DIAGNOSTIC,
        attributes=lambda d: {"version": d.get("miner_version")},
    ),
    # No unit and no state class here: KryptexHashrateSensor picks H/s ... PH/s from the value.
    KryptexDeviceSensorDescription(key="hashrate", field="hashrate", suggested_display_precision=2),
    KryptexDeviceSensorDescription(
        key="daily_income", field="daily_btc", native_unit_of_measurement=BTC,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=8,
    ),
    KryptexDeviceSensorDescription(
        key="temperature", field="temperature", device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=SensorStateClass.MEASUREMENT,
    ),
    KryptexDeviceSensorDescription(
        key="memory_temperature", field="memory_temperature", device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=SensorStateClass.MEASUREMENT,
    ),
    KryptexDeviceSensorDescription(
        key="power", field="power", device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT, state_class=SensorStateClass.MEASUREMENT,
    ),
    KryptexDeviceSensorDescription(
        key="fan", field="fan", native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
    ),
    KryptexDeviceSensorDescription(
        key="core_clock", field="core_clock", device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.MEGAHERTZ, state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    KryptexDeviceSensorDescription(
        key="memory_clock", field="memory_clock", device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.MEGAHERTZ, state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
)


class KryptexDeviceSensor(KryptexDeviceEntity, SensorEntity):
    entity_description: KryptexDeviceSensorDescription

    def __init__(self, coordinator: KryptexCoordinator, device: dict[str, Any], description: KryptexDeviceSensorDescription) -> None:
        super().__init__(coordinator, device, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        device = self.device
        return None if device is None else device.get(self.entity_description.field)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        device = self.device
        return {} if device is None else self.entity_description.attributes(device)


HASHRATE_UNITS = ((1e15, "PH/s"), (1e12, "TH/s"), (1e9, "GH/s"), (1e6, "MH/s"), (1e3, "kH/s"))


def scale_hashrate(value: float) -> tuple[float, str]:
    """Hashrate in H/s as a readable number and unit, like the Kryptex app shows it (e.g. 125.9 TH/s)."""
    for factor, unit in HASHRATE_UNITS:
        if abs(value) >= factor:
            return value / factor, unit
    return value, "H/s"


class KryptexHashrateSensor(KryptexDeviceSensor):
    """Hashrates differ by many orders of magnitude between algorithms (kH/s on a CPU, TH/s on some GPU algorithms),
    and Kryptex can switch algorithms. So the unit follows the value. Without a state class Home Assistant allows the
    unit to change; the history is kept, and the value in H/s is in the attribute hashrate_h_s."""

    def _scaled(self) -> tuple[float, str] | None:
        device = self.device
        value = None if device is None else device.get("hashrate")
        return None if value is None else scale_hashrate(float(value))

    @property
    def native_value(self) -> float | None:
        scaled = self._scaled()
        return None if scaled is None else round(scaled[0], 2)

    @property
    def native_unit_of_measurement(self) -> str | None:
        scaled = self._scaled()
        return "H/s" if scaled is None else scaled[1]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        device = self.device
        return {} if device is None else {"hashrate_h_s": device.get("hashrate")}


class KryptexDeviceCurrencySensor(KryptexDeviceEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: KryptexCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator, device, "daily_income_currency")
        self._attr_native_unit_of_measurement = coordinator.currency
        self._attr_translation_placeholders = {"currency": coordinator.currency}

    @property
    def native_value(self) -> float | None:
        device, price = self.device, self.coordinator.btc_price()
        if device is None or price is None or device.get("daily_btc") is None:
            return None
        return round(device["daily_btc"] * price, 4)


# --- Setup ------------------------------------------------------------------------------------------------------

async def async_setup_entry(
    hass: HomeAssistant, entry: KryptexConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [KryptexSensor(coordinator, d) for d in BTC_SENSORS]
    if coordinator.currency:
        entities += [KryptexCurrencySensor(coordinator, d) for d in BTC_SENSORS]
        entities.append(KryptexBitcoinPriceSensor(coordinator))
        if entry.options.get(CONF_POWER_ENTITY):
            entities.append(KryptexProfitSensor(coordinator, "break_even_price"))
            if entry.options.get(CONF_PRICE_ENTITY):
                entities += [KryptexProfitSensor(coordinator, "electricity_cost"),
                             KryptexProfitSensor(coordinator, "net_profit")]
    async_add_entities(entities)

    # Mining devices come and go with Kryptex: add an entity the first time its device reports the value.
    known: set[tuple[str, str]] = set()

    @callback
    def add_devices() -> None:
        new: list[SensorEntity] = []
        for device in coordinator.devices:
            for description in DEVICE_SENSORS:
                key = (device["id"], description.key)
                if key not in known and device.get(description.field) is not None:
                    known.add(key)
                    sensor_class = KryptexHashrateSensor if description.key == "hashrate" else KryptexDeviceSensor
                    new.append(sensor_class(coordinator, device, description))
            key = (device["id"], "daily_income_currency")
            if coordinator.currency and key not in known and device.get("daily_btc") is not None:
                known.add(key)
                new.append(KryptexDeviceCurrencySensor(coordinator, device))
        if new:
            async_add_entities(new)

    add_devices()
    entry.async_on_unload(coordinator.async_add_listener(add_devices))
