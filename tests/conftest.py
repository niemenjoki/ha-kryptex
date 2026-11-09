"""Fixtures. Bridge answers are saved in tests/fixtures/*.json (synthetic values, device names from a real PC)."""

import copy
import json
from pathlib import Path

import pytest

from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kryptex.const import DOMAIN

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "http://192.0.2.10:5500"
DATA = {CONF_HOST: "192.0.2.10", CONF_PORT: 5500, CONF_API_KEY: "test-key"}


def load(name: str) -> dict:
    return copy.deepcopy(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def make_entry(options: dict | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN, title="Kryptex", data=DATA, unique_id="192.0.2.10:5500", options=options or {}
    )


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def config_entry():
    return make_entry()
