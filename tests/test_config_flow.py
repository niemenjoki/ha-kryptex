from aiohttp import ClientConnectionError

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kryptex.const import DOMAIN

from .conftest import BASE, DATA, load


async def test_user_flow(hass, aioclient_mock):
    aioclient_mock.get(f"{BASE}/health", json={"ok": True, "version": "0.2.0"})
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Kryptex"
    assert result["data"] == DATA
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "Bearer test-key"


async def test_user_flow_errors_then_success(hass, aioclient_mock):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    aioclient_mock.get(f"{BASE}/health", status=401, json={"ok": False, "error": "unauthorized"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/health", exc=ClientConnectionError())
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["errors"] == {"base": "cannot_connect"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/health", json={"ok": True})
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_duplicate_is_aborted(hass, aioclient_mock, config_entry):
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass, aioclient_mock, config_entry):
    config_entry.add_to_hass(hass)
    aioclient_mock.get(f"{BASE}/health", json={"ok": True})
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "new-key"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_API_KEY] == "new-key"
    await hass.async_block_till_done()


async def test_options_default_currency_and_validation(hass, aioclient_mock, config_entry):
    hass.config.currency = "EUR"
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["step_id"] == "init"
    currency = next(key for key in result["data_schema"].schema if key == "currency")
    assert currency.default() == "eur"  # Home Assistant's own currency, when Kryptex has a rate for it.

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"currency": "none", "power_entity": "sensor.pc_power", "price_unit": "currency_per_kwh"}
    )
    assert result["errors"] == {"base": "currency_required"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"currency": "eur", "price_entity": "sensor.price", "price_unit": "currency_per_kwh"}
    )
    assert result["errors"] == {"base": "power_required"}
    options = {"currency": "eur", "power_entity": "sensor.pc_power", "price_entity": "sensor.price", "price_unit": "cents_per_kwh"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], options)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert config_entry.options == options
    # The entry reloads with the new options.
    assert hass.states.get("sensor.kryptex_balance_in_eur") is not None


async def test_options_bitcoin_only(hass, aioclient_mock, config_entry):
    hass.config.currency = "SEK"  # Not in Kryptex's rates of the fixture: default is bitcoin only.
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    currency = next(key for key in result["data_schema"].schema if key == "currency")
    assert currency.default() == "none"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"currency": "none", "price_unit": "currency_per_kwh"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_reconfigure_keeps_the_key_when_left_empty(hass, aioclient_mock, config_entry):
    config_entry.add_to_hass(hass)
    new = "http://192.0.2.11:5501"
    aioclient_mock.get(f"{new}/health", json={"ok": True})
    aioclient_mock.get(f"{new}/status", json=load("status_mining.json"))
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "192.0.2.11", "port": 5501})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data == {"host": "192.0.2.11", "port": 5501, CONF_API_KEY: "test-key"}
    assert config_entry.unique_id == "192.0.2.11:5501"
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "Bearer test-key"
    await hass.async_block_till_done()


async def test_reconfigure_errors_and_new_key(hass, aioclient_mock, config_entry):
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    aioclient_mock.get(f"{BASE}/health", exc=ClientConnectionError())
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "192.0.2.10", "port": 5500})
    assert result["errors"] == {"base": "cannot_connect"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/health", json={"ok": True})
    aioclient_mock.get(f"{BASE}/status", json=load("status_mining.json"))
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "192.0.2.10", "port": 5500, CONF_API_KEY: "new-key"}
    )
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_API_KEY] == "new-key"
    await hass.async_block_till_done()


async def test_reconfigure_to_another_configured_bridge_is_aborted(hass, aioclient_mock, config_entry):
    config_entry.add_to_hass(hass)
    other = MockConfigEntry(domain=DOMAIN, title="Kryptex", unique_id="192.0.2.12:5500",
                            data={"host": "192.0.2.12", "port": 5500, CONF_API_KEY: "k"})
    other.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "192.0.2.12", "port": 5500})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
