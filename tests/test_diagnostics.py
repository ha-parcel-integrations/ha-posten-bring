"""Tests for Posten Bring diagnostics."""
from datetime import timedelta
from unittest.mock import MagicMock

from custom_components.posten_bring.diagnostics import (
    async_get_config_entry_diagnostics,
)


def _base_entry() -> MagicMock:
    entry = MagicMock()
    entry.data = {"brand": "bring", "refresh_token": "the-refresh-token"}
    entry.options = {}
    entry.runtime_data.coordinator.current_tier_minutes = 15
    entry.runtime_data.coordinator.update_interval = timedelta(minutes=15)
    entry.runtime_data.coordinator.outgoing = []
    entry.runtime_data.coordinator.delivered_outgoing = []
    return entry


async def test_diagnostics_redacts_the_refresh_token(hass):
    entry = _base_entry()
    entry.runtime_data.coordinator.data = []
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entry_data"]["refresh_token"] == "**REDACTED**"
    assert result["entry_data"]["brand"] == "bring"


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    entry = _base_entry()
    entry.runtime_data.coordinator.data = [
        {
            "barcode": "PB123456789NO",
            "sender": "Example Shop",
            "receiver": "Jane Doe",
            "status": "in_transit",
            "raw": {
                "parcelNumber": "PB123456789NO",
                "recipient": {"name": "Jane Doe", "address": "Coolsingel 1"},
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {
        "incoming_active": 1,
        "delivered": 0,
        "outgoing_active": 0,
        "outgoing_delivered": 0,
    }
    assert result["polling"] == {
        "tier_minutes": 15,
        "update_interval_seconds": 900.0,
        "suspended": False,
    }
    # PII is redacted, at every nesting level
    assert result["incoming"][0]["barcode"] == "**REDACTED**"
    assert result["incoming"][0]["receiver"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["recipient"] == "**REDACTED**"
    # non-identifying fields survive, or the diagnostics would be useless
    assert result["incoming"][0]["status"] == "in_transit"


async def test_diagnostics_reports_suspended_polling(hass):
    """update_interval None must be visible, not just absent."""
    entry = _base_entry()
    entry.runtime_data.coordinator.current_tier_minutes = None
    entry.runtime_data.coordinator.update_interval = None
    entry.runtime_data.coordinator.data = []
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["polling"] == {
        "tier_minutes": None,
        "update_interval_seconds": None,
        "suspended": True,
    }


async def test_diagnostics_reports_outgoing_counts(hass):
    entry = _base_entry()
    entry.runtime_data.coordinator.data = []
    entry.runtime_data.coordinator.delivered = []
    entry.runtime_data.coordinator.outgoing = [{"barcode": "O1"}]
    entry.runtime_data.coordinator.delivered_outgoing = [{"barcode": "O2"}]

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"]["outgoing_active"] == 1
    assert result["counts"]["outgoing_delivered"] == 1
    assert result["outgoing"][0]["barcode"] == "**REDACTED**"
    assert result["outgoing_delivered"][0]["barcode"] == "**REDACTED**"
