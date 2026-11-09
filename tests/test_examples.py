"""The files in examples/ are valid and the pause automation does what it says."""

from datetime import timedelta
from pathlib import Path

from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import parse_yaml
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .conftest import BASE, load, make_entry

EXAMPLES = Path(__file__).parents[1] / "examples"


def example(name: str):
    text = (EXAMPLES / name).read_text(encoding="utf-8")
    return parse_yaml(text)


def test_dashboard_examples_parse():
    for name in ("dashboard-overview.yaml", "dashboard-device.yaml"):
        card = example(name)
        assert card["type"] == "grid" and card["cards"]


async def test_pause_automation(hass, aioclient_mock, freezer):
    hass.states.async_set("sensor.pc_power", "800", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.electricity_total_price", "2", {"unit_of_measurement": "c/kWh"})
    entry = make_entry({"currency": "eur", "power_entity": "sensor.pc_power",
                        "price_entity": "sensor.electricity_total_price", "price_unit": "cents_per_kwh"})
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    aioclient_mock.post(f"{BASE}/pause", json={"ok": True, "changed": True, "paused": True})
    aioclient_mock.post(f"{BASE}/resume", json={"ok": True, "changed": True, "paused": False})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await async_setup_component(hass, "automation", {"automation": [example("automation-pause-when-unprofitable.yaml")]})
    await hass.async_block_till_done()

    def posts():
        return [str(call[1]).rsplit("/", 1)[1] for call in aioclient_mock.mock_calls if call[0] == "POST"]

    # Break-even 3.75 c/kWh. Price rises to 5 c/kWh: pause after 10 minutes, not before.
    hass.states.async_set("sensor.electricity_total_price", "5", {"unit_of_measurement": "c/kWh"})
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=9))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert posts() == []
    freezer.tick(timedelta(minutes=2))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert posts() == ["pause"]

    # Price falls to 3 c/kWh: resume after 10 minutes.
    hass.states.async_set("sensor.electricity_total_price", "3", {"unit_of_measurement": "c/kWh"})
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert posts() == ["pause", "resume"]
