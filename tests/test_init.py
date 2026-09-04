"""Tests for Posten Bring setup and unload."""
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.posten_bring.api import (
    PostenBringApiError,
    PostenBringAuthError,
)
from custom_components.posten_bring.const import CONF_BRAND, CONF_REFRESH_TOKEN, DOMAIN

from .payloads import active_parcel

CLIENT = "custom_components.posten_bring.api.PostenBringApiClient"


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Bring",
        data={CONF_BRAND: "bring", CONF_REFRESH_TOKEN: "the-refresh-token"},
    )


async def test_setup_and_unload(hass):
    entry = _entry()
    entry.add_to_hass(hass)

    with patch(
        f"{CLIENT}.async_get_parcels", new=AsyncMock(return_value=[active_parcel()])
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED

    incoming = hass.states.get("sensor.posten_bring_bring_incoming_parcels")
    assert incoming is not None
    assert incoming.state == "1"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_rejected_refresh_token_starts_reauth(hass):
    entry = _entry()
    entry.add_to_hass(hass)

    with patch(
        f"{CLIENT}.async_get_parcels",
        new=AsyncMock(side_effect=PostenBringAuthError("HTTP 401")),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


@pytest.mark.parametrize("error", [PostenBringApiError("HTTP 500")])
async def test_outage_retries_instead_of_reauth(hass, error):
    """A 5xx must retry with backoff — never push the user into reauth."""
    entry = _entry()
    entry.add_to_hass(hass)

    with patch(f"{CLIENT}.async_get_parcels", new=AsyncMock(side_effect=error)):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress()


async def test_failed_platform_setup_closes_the_session(hass):
    """Every failed-setup path must close the per-entry session, or each retry
    leaks one."""
    entry = _entry()
    entry.add_to_hass(hass)

    with (
        patch(
            f"{CLIENT}.async_get_parcels", new=AsyncMock(return_value=[active_parcel()])
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(side_effect=RuntimeError("platform blew up")),
        ),
        patch("aiohttp.ClientSession.close", new=AsyncMock()) as close,
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    close.assert_awaited()


async def test_per_parcel_sensor_spawn_and_remove(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    barcode = active_parcel()["parcelNumber"]

    parcels = AsyncMock(return_value=[active_parcel()])
    with patch(f"{CLIENT}.async_get_parcels", new=parcels):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        registry = er.async_get(hass)
        assert registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_{barcode}"
        )

        # The next poll returns a different parcel: the summary sensor spawns a
        # new per-parcel sensor and removes the stale one via the registry.
        parcels.return_value = [active_parcel("PB000000002NO")]
        await entry.runtime_data.coordinator.async_request_refresh()
        await hass.async_block_till_done()

        assert registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_PB000000002NO"
        )
        assert (
            registry.async_get_entity_id(
                "sensor", DOMAIN, f"{entry.entry_id}_{barcode}"
            )
            is None
        )


async def test_rotated_refresh_token_survives_a_failing_first_poll(hass):
    """Setup fails after the refresh rotated — the entry must hold the new token.

    Otherwise the retry (and every restart after it) reuses the token the
    identity provider already burned, and a transient outage costs the user
    the whole browser-paste login.
    """
    entry = _entry()
    entry.add_to_hass(hass)

    rotated = {
        "access_token": "AT-NEW",
        "refresh_token": "RT-NEW",
        "expires_in": 3600,
    }
    with (
        patch(
            "custom_components.posten_bring.api.PostenBringSession._async_post_token",
            new=AsyncMock(return_value=rotated),
        ),
        patch(
            "custom_components.posten_bring.api.PostenBringApiClient._async_do_request",
            new=AsyncMock(return_value=(None, 500, None)),
        ),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.data[CONF_REFRESH_TOKEN] == "RT-NEW"
