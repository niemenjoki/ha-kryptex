"""Constants for the Kryptex integration."""

from datetime import timedelta

DOMAIN = "kryptex"
DEFAULT_PORT = 5500
SCAN_INTERVAL = timedelta(seconds=60)

# Options
CONF_CURRENCY = "currency"
CONF_POWER_ENTITY = "power_entity"
CONF_PRICE_ENTITY = "price_entity"
CONF_PRICE_UNIT = "price_unit"
NO_CURRENCY = "none"
PRICE_PER_KWH = "currency_per_kwh"
PRICE_CENTS_PER_KWH = "cents_per_kwh"
