from datetime import timedelta

from aiohttp import ClientConnectionError
import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.kryptex import async_remove_config_entry_device
from custom_components.kryptex.const import DOMAIN

from .conftest import BASE, load, make_entry

MAIN = "kryptex"
GPU = "nvidia_geforce_rtx_3080_ti"
CPU = "amd_ryzen_9_5900x_12_core_processor"


async def setup(hass, config_entry):
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def refresh(hass):
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()


def state(hass, entity_id):
    return hass.states.get(entity_id)


async def test_amounts_are_in_bitcoin(hass, aioclient_mock, config_entry):
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    await setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    balance = state(hass, f"sensor.{MAIN}_balance")
    assert (balance.state, balance.attributes["unit_of_measurement"]) == ("0.00012345", "BTC")
    assert state(hass, f"sensor.{MAIN}_withdrawable_balance").state == "0.0001"
    assert state(hass, f"sensor.{MAIN}_daily_income").state == "8e-06"
    assert state(hass, f"sensor.{MAIN}_monthly_income").state == "0.00024"
    assert len(state(hass, f"sensor.{MAIN}_daily_income").attributes["devices"]) == 2
    # No display currency chosen: no converted sensors and no profitability.
    assert not [s for s in hass.states.async_entity_ids("sensor") if "eur" in s or "usd" in s or "price" in s]
    assert state(hass, f"binary_sensor.{MAIN}_app_running").state == STATE_ON
    assert state(hass, f"binary_sensor.{MAIN}_mining").state == STATE_ON
    assert state(hass, f"switch.{MAIN}_kryptex_app").state == STATE_ON
    assert state(hass, f"switch.{MAIN}_paused").state == STATE_OFF


async def test_mining_devices_are_own_devices(hass, aioclient_mock, config_entry):
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    await setup(hass, config_entry)

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), config_entry.entry_id)
    assert sorted(d.name for d in devices) == ["AMD Ryzen 9 5900X 12-Core Processor", "Kryptex", "NVIDIA GeForce RTX 3080 Ti"]
    gpu = next(d for d in devices if d.name == "NVIDIA GeForce RTX 3080 Ti")
    assert (gpu.manufacturer, gpu.hw_version) == ("NVIDIA", "Ampere")
    assert gpu.via_device_id == next(d.id for d in devices if d.name == "Kryptex")

    assert state(hass, f"sensor.{GPU}_temperature").state == "83.0"
    assert state(hass, f"sensor.{GPU}_power").state == "339.0"
    assert state(hass, f"sensor.{GPU}_fan").state == "100.0"
    assert state(hass, f"sensor.{GPU}_state").state == "mining"
    assert state(hass, f"sensor.{GPU}_algorithm").attributes["coin"] == "prl"
    assert state(hass, f"sensor.{GPU}_miner").attributes["version"] == "v2.20.5"
    assert state(hass, f"sensor.{GPU}_daily_income").state == "2.55e-05"
    # Kryptex reports no CPU temperature or power: those entities are not created at all.
    assert state(hass, f"sensor.{CPU}_temperature") is None
    assert state(hass, f"sensor.{CPU}_power") is None
    hashrate = state(hass, f"sensor.{CPU}_hashrate")
    assert (hashrate.state, hashrate.attributes["unit_of_measurement"]) == ("11.11", "kH/s")
    assert hashrate.attributes["hashrate_h_s"] == 11106.02
    assert "state_class" not in hashrate.attributes
    # Clocks are disabled by default.
    assert state(hass, f"sensor.{GPU}_core_clock") is None
    assert er.async_get(hass).async_get(f"sensor.{GPU}_core_clock").disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_new_device_is_added_and_missing_one_can_be_removed(hass, aioclient_mock, config_entry):
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    await setup(hass, config_entry)

    status = load("status_mining.json")
    gpu2 = {**status["service"]["devices"][1], "id": "4", "name": "NVIDIA GeForce RTX 3060", "temperature": 72.0}
    status["service"]["devices"] = [status["service"]["devices"][0], gpu2]  # The 3080 Ti is gone, a 3060 appears.
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/status", json=status)
    await refresh(hass)

    assert state(hass, "sensor.nvidia_geforce_rtx_3060_temperature").state == "72.0"
    assert state(hass, f"sensor.{GPU}_temperature").state == STATE_UNAVAILABLE
    registry = dr.async_get(hass)
    old = registry.async_get_device(identifiers={(DOMAIN, f"{config_entry.entry_id}_2")})
    new = registry.async_get_device(identifiers={(DOMAIN, f"{config_entry.entry_id}_4")})
    main = registry.async_get_device(identifiers={(DOMAIN, config_entry.entry_id)})
    assert await async_remove_config_entry_device(hass, config_entry, old) is True
    assert await async_remove_config_entry_device(hass, config_entry, new) is False
    assert await async_remove_config_entry_device(hass, config_entry, main) is False


async def test_display_currency(hass, aioclient_mock):
    entry = make_entry({"currency": "eur", "price_unit": "currency_per_kwh"})
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    await setup(hass, entry)

    balance = state(hass, f"sensor.{MAIN}_balance_in_eur")
    assert (balance.state, balance.attributes["unit_of_measurement"]) == ("11.1105", "EUR")
    assert balance.attributes["device_class"] == "monetary"
    assert state(hass, f"sensor.{MAIN}_monthly_income_in_eur").state == "21.6"
    assert state(hass, f"sensor.{MAIN}_bitcoin_price").state == "90000.0"
    assert state(hass, f"sensor.{GPU}_daily_income_in_eur").state == "2.295"
    # The bitcoin values stay.
    assert state(hass, f"sensor.{MAIN}_balance").state == "0.00012345"
    # No power sensor chosen: no profitability.
    assert state(hass, f"sensor.{MAIN}_break_even_electricity_price") is None


async def test_profitability(hass, aioclient_mock):
    assert await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set("sensor.pc_power", "800", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.total_price", "10", {"unit_of_measurement": "c/kWh"})
    entry = make_entry({"currency": "eur", "power_entity": "sensor.pc_power", "price_entity": "sensor.total_price",
                        "price_unit": "cents_per_kwh"})
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    await setup(hass, entry)

    # Income 0.000008 BTC x 90 000 = 0.72 EUR/day; 800 W = 19.2 kWh/day.
    break_even = state(hass, f"sensor.{MAIN}_break_even_electricity_price")
    assert (break_even.state, break_even.attributes["unit_of_measurement"]) == ("3.75", "c/kWh")
    assert state(hass, f"sensor.{MAIN}_electricity_cost_per_day").state == "1.92"
    assert state(hass, f"sensor.{MAIN}_net_profit_per_day").state == "-1.2"

    # Follows the power sensor without waiting for the next poll.
    hass.states.async_set("sensor.pc_power", "0.4", {"unit_of_measurement": "kW"})
    await hass.async_block_till_done()
    assert state(hass, f"sensor.{MAIN}_break_even_electricity_price").state == "7.5"
    hass.states.async_set("sensor.pc_power", "unavailable")
    await hass.async_block_till_done()
    assert state(hass, f"sensor.{MAIN}_net_profit_per_day").state == STATE_UNAVAILABLE


def paused_status(estimate: bool = True) -> dict:
    status = load("status_mining.json")
    status["profitability"] = {"ok": False, "error": "paused", "message": "Kryptex is paused."}
    status["service"]["paused"] = True
    if not estimate:
        status["estimate"] = {"ok": False, "error": "stale_measurement", "message": "x"}
    return status


async def poll(hass, aioclient_mock, status: dict, power: str | None = None):
    if power is not None:
        hass.states.async_set("sensor.pc_power", power, {"unit_of_measurement": "W"})
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/status", json=status)
    await refresh(hass)


async def test_mining_and_app_running(hass, aioclient_mock, config_entry):
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    await setup(hass, config_entry)
    assert state(hass, f"binary_sensor.{MAIN}_mining").state == STATE_ON

    await poll(hass, aioclient_mock, paused_status())
    # Paused: the app still runs, but it does not mine.
    assert state(hass, f"binary_sensor.{MAIN}_app_running").state == STATE_ON
    assert state(hass, f"binary_sensor.{MAIN}_mining").state == STATE_OFF

    await poll(hass, aioclient_mock, load("status_stopped.json"))
    assert state(hass, f"binary_sensor.{MAIN}_app_running").state == STATE_OFF
    assert state(hass, f"binary_sensor.{MAIN}_mining").state == STATE_OFF


async def test_profitability_while_paused(hass, aioclient_mock, hass_storage, freezer):
    """Paused: income from the bridge's estimate, power from the median while mining, not the idle power."""
    hass.states.async_set("sensor.pc_power", "800", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.total_price", "10", {"unit_of_measurement": "c/kWh"})
    entry = make_entry({"currency": "eur", "power_entity": "sensor.pc_power", "price_entity": "sensor.total_price",
                        "price_unit": "cents_per_kwh"})
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    await setup(hass, entry)

    # Samples are taken from the second mining poll on: 800 W and 700 W, median 750 W = 18 kWh/day.
    await poll(hass, aioclient_mock, load("status_mining.json"))
    await poll(hass, aioclient_mock, load("status_mining.json"), power="700")
    # The pause begins; the power falls before the bridge reports the pause. No sample is taken.
    hass.states.async_set("sensor.pc_power", "170", {"unit_of_measurement": "W"})
    await poll(hass, aioclient_mock, paused_status())

    # Estimate 0.000008 BTC x 90 000 = 0.72 EUR/day; 0.72 / 18 kWh = 4 c/kWh.
    break_even = state(hass, f"sensor.{MAIN}_break_even_electricity_price")
    assert break_even.state == "4.0"
    assert break_even.attributes["income_basis"] == "estimate"
    assert break_even.attributes["power_basis"] == "median of 2 samples while mining"
    assert break_even.attributes["hashrate_measured_at"] == "2026-09-21T13:33:15.000Z"
    assert state(hass, f"sensor.{MAIN}_net_profit_per_day").state == "-1.08"

    # The samples are kept over a restart of Home Assistant.
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass_storage[f"kryptex.{entry.entry_id}"]["data"]["power_samples"] == [800.0, 700.0]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert state(hass, f"sensor.{MAIN}_break_even_electricity_price").state == "4.0"

    # No estimate (e.g. Kryptex has not mined for a week): no profitability.
    await poll(hass, aioclient_mock, paused_status(estimate=False))
    assert state(hass, f"sensor.{MAIN}_break_even_electricity_price").state == STATE_UNAVAILABLE

    # Removing the integration deletes the samples.
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert f"kryptex.{entry.entry_id}" not in hass_storage


async def test_profitability_paused_before_any_sample(hass, aioclient_mock):
    """Set up while paused: the idle power now must not be used, so there is no profitability yet."""
    hass.states.async_set("sensor.pc_power", "170", {"unit_of_measurement": "W"})
    entry = make_entry({"currency": "eur", "power_entity": "sensor.pc_power", "price_unit": "cents_per_kwh"})
    aioclient_mock.get(f"{BASE}/status", json=paused_status())
    await setup(hass, entry)
    assert state(hass, f"sensor.{MAIN}_break_even_electricity_price").state == STATE_UNAVAILABLE


async def test_profitability_without_price(hass, aioclient_mock):
    hass.states.async_set("sensor.pc_power", "720", {"unit_of_measurement": "W"})
    entry = make_entry({"currency": "eur", "power_entity": "sensor.pc_power", "price_unit": "currency_per_kwh"})
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    await setup(hass, entry)
    # 0.72 EUR / 17.28 kWh
    break_even = state(hass, f"sensor.{MAIN}_break_even_electricity_price")
    assert (break_even.state, break_even.attributes["unit_of_measurement"]) == ("0.0417", "EUR/kWh")
    assert state(hass, f"sensor.{MAIN}_net_profit_per_day") is None


async def test_update_is_shown_but_not_installable(hass, aioclient_mock, config_entry):
    status = load("status_mining.json")
    aioclient_mock.get(f"{BASE}/status", json=status)
    await setup(hass, config_entry)
    update = state(hass, f"update.{MAIN}_kryptex")
    assert (update.state, update.attributes["installed_version"]) == (STATE_OFF, "5.10.7")
    assert update.attributes["supported_features"] == 0
    assert "administrator" in update.attributes["release_summary"]

    for offered, latest in (({"available": True, "version": "5.11.0"}, "5.11.0"), ({"available": True, "version": None}, "unknown")):
        status["service"]["update"] = offered
        aioclient_mock.clear_requests()
        aioclient_mock.get(f"{BASE}/status", json=status)
        await refresh(hass)
        update = state(hass, f"update.{MAIN}_kryptex")
        assert (update.state, update.attributes["latest_version"]) == (STATE_ON, latest)


async def test_failed_reading_only_affects_its_sensors(hass, aioclient_mock, config_entry):
    aioclient_mock.get(f"{BASE}/status", json=load("status_stopped.json"))
    await setup(hass, config_entry)
    assert state(hass, f"sensor.{MAIN}_daily_income").state == STATE_UNAVAILABLE
    assert state(hass, f"sensor.{MAIN}_balance").state == "0.00012345"
    assert state(hass, f"switch.{MAIN}_kryptex_app").state == STATE_OFF
    assert state(hass, f"switch.{MAIN}_paused").state == STATE_UNAVAILABLE
    assert state(hass, f"update.{MAIN}_kryptex").state == STATE_UNAVAILABLE


async def test_pc_off_at_startup_then_on(hass, aioclient_mock, config_entry):
    aioclient_mock.get(f"{BASE}/status", exc=ClientConnectionError())
    await setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert state(hass, f"switch.{MAIN}_kryptex_app").state == STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    await refresh(hass)
    assert state(hass, f"switch.{MAIN}_kryptex_app").state == STATE_ON
    assert state(hass, f"sensor.{GPU}_temperature").state == "83.0"


async def test_rejected_key_starts_reauth(hass, aioclient_mock, config_entry):
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    await setup(hass, config_entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/status", status=401, json={"ok": False, "error": "unauthorized"})
    await refresh(hass)
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_switches_call_bridge(hass, aioclient_mock, config_entry):
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    for path in ("/start", "/stop"):
        aioclient_mock.post(f"{BASE}{path}", json={"ok": True, "changed": True})
    aioclient_mock.post(f"{BASE}/pause", json={"ok": True, "changed": True, "paused": True})
    aioclient_mock.post(f"{BASE}/resume", json={"ok": True, "changed": True, "paused": False})
    await setup(hass, config_entry)

    for service, entity in (("turn_on", "kryptex_app"), ("turn_off", "kryptex_app"), ("turn_on", "paused"), ("turn_off", "paused")):
        await hass.services.async_call("switch", service, {"entity_id": f"switch.{MAIN}_{entity}"}, blocking=True)
    posts = [str(call[1]) for call in aioclient_mock.mock_calls if call[0] == "POST"]
    assert posts == [f"{BASE}/start", f"{BASE}/stop", f"{BASE}/pause", f"{BASE}/resume"]


async def test_switch_error_is_shown(hass, aioclient_mock, config_entry):
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    aioclient_mock.post(f"{BASE}/pause", status=500,
                        json={"ok": False, "error": "pause_not_applied", "message": "Kryptex did not change its pause state."})
    await setup(hass, config_entry)
    with pytest.raises(HomeAssistantError, match="did not change its pause state"):
        await hass.services.async_call("switch", "turn_on", {"entity_id": f"switch.{MAIN}_paused"}, blocking=True)


async def test_unload(hass, aioclient_mock, config_entry):
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    await setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


def test_hashrate_units():
    from custom_components.kryptex.sensor import scale_hashrate

    assert scale_hashrate(134_839_274_728_448) == (134.839274728448, "TH/s")
    assert scale_hashrate(10_300) == (10.3, "kH/s")
    assert scale_hashrate(52_820_000_000_000)[1] == "TH/s"
    assert scale_hashrate(950) == (950, "H/s")
    assert scale_hashrate(0) == (0, "H/s")


def test_every_entity_has_an_icon_or_device_class():
    import json
    from pathlib import Path

    base = Path(__file__).parents[1] / "custom_components" / "kryptex"
    names = json.loads((base / "strings.json").read_text(encoding="utf-8"))["entity"]
    icons = json.loads((base / "icons.json").read_text(encoding="utf-8"))["entity"]
    with_device_class = {"device_temperature", "device_memory_temperature", "device_power", "app_update"}
    for platform, keys in names.items():
        for key in keys:
            assert key in icons.get(platform, {}) or key in with_device_class, (platform, key)
