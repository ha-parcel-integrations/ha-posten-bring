"""The device every entity of this integration belongs to.

One place, because sensors, the button and the calendar must all land on the
*same* device entry — and because the device is named per account here, an
account-based integration can have several.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN

CONFIGURATION_URL = "https://www.postenbring.no"

ATTRIBUTION = "Data provided by Posten Bring"


def build_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the DeviceInfo shared by every entity of this account.

    ``entry.title`` is the brand name ("Posten"/"Bring") set at config-flow
    time — this API's token response carries no stable account identifier
    (no ID token, no profile call; see config_flow.py) to disambiguate two
    accounts of the *same* brand beyond that, a known, documented gap.
    Entities inherit the device name via ``has_entity_name``, yielding names
    like "Posten Bring (Posten) Incoming parcels".
    """
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Posten Bring ({entry.title})",
        manufacturer="Posten Bring",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=CONFIGURATION_URL,
    )
