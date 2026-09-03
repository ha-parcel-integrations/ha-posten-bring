"""Diagnostics support for the Posten Bring parcel tracker integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import PostenBringConfigEntry

# Diagnostics are pasted into public issues, so redact anything that
# identifies a person, an address or a specific parcel. Over-redacting is
# cheap; under-redacting leaks a user's home address into a GitHub thread.
# The "do not build" list in the build plan explicitly forbids tokens, the
# client_secret, names, addresses, barcodes or tracking URLs anywhere in
# diagnostics/logs/events.
TO_REDACT = {
    # canonical fields we publish ourselves
    "barcode",
    "sender",
    "receiver",
    "url",
    "pickup_point",
    # OAuth credential fields — never leave this file even redacted-adjacent
    "refresh_token",
    "access_token",
    "client_secret",
    "client_id",
    "code",
    "state",
    # carrier payload fields
    "parcelNumber",
    "consignmentNumber",
    "recipient",
    "name",
    "address",
    "postalCode",
    "city",
    "street",
    "email",
    "phone",
    "userChosenName",
    "alias",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PostenBringConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the Posten Bring config entry."""
    coordinator = entry.runtime_data.coordinator

    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "counts": {
            "incoming_active": len(coordinator.data or []),
            "delivered": len(coordinator.delivered or []),
            "outgoing_active": len(coordinator.outgoing or []),
            "outgoing_delivered": len(coordinator.delivered_outgoing or []),
        },
        "polling": {
            "tier_minutes": coordinator.current_tier_minutes,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "suspended": coordinator.update_interval is None,
        },
        "incoming": async_redact_data(coordinator.data or [], TO_REDACT),
        "delivered": async_redact_data(coordinator.delivered or [], TO_REDACT),
        "outgoing": async_redact_data(coordinator.outgoing or [], TO_REDACT),
        "outgoing_delivered": async_redact_data(
            coordinator.delivered_outgoing or [], TO_REDACT
        ),
    }
