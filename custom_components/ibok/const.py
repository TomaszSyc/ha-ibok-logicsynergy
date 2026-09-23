"""Constants for the iBOK integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "ibok"

CONF_BASE_URL: Final = "base_url"
# One source entity per meter, keyed by serial: a household with a garden
# sub-meter has two, and they must never be fed from the same sensor.
CONF_SOURCE_ENTITY_PREFIX: Final = "source_entity_"

# The portal publishes meter readings and invoices at most once a day, so there
# is nothing to gain from polling often. A short interval would only add load to
# an operator's server for no new data -- and their terms of use forbid actions
# that could disrupt the service.
DEFAULT_SCAN_INTERVAL: Final = timedelta(hours=6)
MIN_SCAN_INTERVAL_HOURS: Final = 1

# Portal modules. Names are case sensitive: "Meters_v1" returns data while
# "meters_v1" returns an empty body with HTTP 200.
MODULE_MENU: Final = "Menu"
MODULE_METERS: Final = "Meters_v1"
MODULE_READOUTS: Final = "Readouts_v1"
MODULE_NOTIFY_READOUT: Final = "NotifyReadout_v1"
MODULE_ACCOUNTANCY: Final = "Accountancy_v4"
MODULE_INVOICES: Final = "Invoices_v1"

SERVICE_SUBMIT_READING: Final = "submit_reading"

ATTR_METER_ID: Final = "meter_id"
ATTR_READING: Final = "reading"
ATTR_READING_DATE: Final = "reading_date"
ATTR_NOTE: Final = "note"
