"""Tests for the Posten Bring coordinator: fetching, direction split, events.

The parcel mapping itself is covered by ``test_parcels.py``.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.posten_bring.api import PostenBringAuthError, PostenBringSession
from custom_components.posten_bring.const import (
    CONF_BRAND,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    ParcelStatus,
)
from custom_components.posten_bring.coordinator import PostenBringCoordinator

from .payloads import (
    ACTIVE_NUMBER,
    active_parcel,
    archived_delivered_parcel,
    outgoing_parcel,
)


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Bring",
        data={CONF_BRAND: "bring", CONF_REFRESH_TOKEN: "RT-OLD"},
        # Keep-most-recent-100 so the delivered-retention filter never trims
        # the (old, fixed-date) sample parcels these tests assert on.
        options={
            CONF_DELIVERED_FILTER_TYPE: "parcels",
            CONF_DELIVERED_FILTER_AMOUNT: 100,
        },
    )


def _oauth() -> MagicMock:
    oauth = MagicMock(spec=PostenBringSession)
    oauth.pop_refresh_token_changed = MagicMock(return_value=False)
    oauth.refresh_token = "RT-OLD"
    return oauth


def _coordinator(hass, entry, client, oauth=None) -> PostenBringCoordinator:
    return PostenBringCoordinator(hass, client, entry, oauth_session=oauth or _oauth())


def _underway(number: str = ACTIVE_NUMBER) -> dict:
    parcel = active_parcel(number)
    parcel["status"] = "underway"
    return parcel


def _collectable(number: str = ACTIVE_NUMBER) -> dict:
    parcel = active_parcel(number)
    parcel["status"] = "collectable"
    parcel["pickupPoint"] = {"name": "Example Point"}
    return parcel


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------


async def test_update_splits_active_and_delivered(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.return_value = [active_parcel(), archived_delivered_parcel()]
    coordinator = _coordinator(hass, entry, client)

    data = await coordinator._async_update_data()

    assert [parcel["barcode"] for parcel in data] == [active_parcel()["parcelNumber"]]
    assert len(coordinator.delivered) == 1
    assert coordinator.last_success_time is not None


async def test_update_handles_an_empty_account(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.return_value = []
    coordinator = _coordinator(hass, entry, client)

    assert await coordinator._async_update_data() == []
    assert coordinator.outgoing == []
    assert coordinator.delivered_outgoing == []


async def test_expired_session_triggers_reauth(hass):
    """An expired session must start reauth, not retry forever."""
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.side_effect = PostenBringAuthError("HTTP 401")
    coordinator = _coordinator(hass, entry, client)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_rotated_refresh_token_is_persisted(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.return_value = []
    oauth = _oauth()
    oauth.pop_refresh_token_changed = MagicMock(return_value=True)
    oauth.refresh_token = "RT-NEW"
    coordinator = _coordinator(hass, entry, client, oauth)

    await coordinator._async_update_data()

    assert entry.data[CONF_REFRESH_TOKEN] == "RT-NEW"


# ---------------------------------------------------------------------------
# direction split
# ---------------------------------------------------------------------------


async def test_update_splits_incoming_and_outgoing(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.return_value = [active_parcel(), outgoing_parcel()]
    coordinator = _coordinator(hass, entry, client)

    data = await coordinator._async_update_data()

    assert [p["barcode"] for p in data] == [active_parcel()["parcelNumber"]]
    assert [p["barcode"] for p in coordinator.outgoing] == [
        outgoing_parcel()["parcelNumber"]
    ]


async def test_outgoing_fires_status_changed_but_not_registered(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = _coordinator(hass, entry, client)

    registered = []
    changed = []
    hass.bus.async_listen(
        f"{DOMAIN}_outgoing_parcel_status_changed", lambda e: changed.append(e)
    )
    hass.bus.async_listen(f"{DOMAIN}_parcel_registered", lambda e: registered.append(e))

    underway = outgoing_parcel()
    underway["status"] = "underway"
    client.async_get_parcels.return_value = [underway]
    await coordinator._async_update_data()  # first refresh: suppressed

    collectable = outgoing_parcel()
    collectable["status"] = "collectable"
    client.async_get_parcels.return_value = [collectable]
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(changed) == 1
    assert changed[0].data["old_status"] == ParcelStatus.IN_TRANSIT
    assert changed[0].data["new_status"] == ParcelStatus.AT_PICKUP_POINT
    assert registered == []  # outgoing never fires "registered"


async def test_outgoing_delivery_fires_only_delivered(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = _coordinator(hass, entry, client)

    delivered = []
    changed = []
    hass.bus.async_listen(
        f"{DOMAIN}_outgoing_parcel_delivered", lambda e: delivered.append(e)
    )
    hass.bus.async_listen(
        f"{DOMAIN}_outgoing_parcel_status_changed", lambda e: changed.append(e)
    )

    client.async_get_parcels.return_value = [outgoing_parcel()]
    await coordinator._async_update_data()

    delivered_raw = outgoing_parcel()
    delivered_raw["status"] = "archived"
    delivered_raw["events"].append(
        {
            "type": "delivered",
            "date": "2026-05-01T10:00:00Z",
            "description": "delivered",
            "cause": None,
            "countryCode": "NO",
            "displayStatus": "significant",
        }
    )
    client.async_get_parcels.return_value = [delivered_raw]
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert changed == []
    assert len(delivered) == 1


# ---------------------------------------------------------------------------
# events (incoming)
# ---------------------------------------------------------------------------


async def test_first_refresh_fires_nothing(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.return_value = [active_parcel()]
    coordinator = _coordinator(hass, entry, client)

    fired = []
    for suffix in (
        "parcel_registered",
        "parcel_status_changed",
        "parcel_delivered",
        "parcel_delivery_time_changed",
    ):
        hass.bus.async_listen(f"{DOMAIN}_{suffix}", lambda e: fired.append(e))

    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert fired == []


async def test_event_carries_device_id(hass):
    from homeassistant.helpers import device_registry as dr

    entry = _entry()
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
    )
    client = AsyncMock()
    coordinator = _coordinator(hass, entry, client)

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_status_changed", lambda e: events.append(e)
    )

    client.async_get_parcels.return_value = [_underway()]
    await coordinator._async_update_data()
    client.async_get_parcels.return_value = [_collectable()]
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert events[0].data["device_id"] == device.id


async def test_fires_status_changed_event(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = _coordinator(hass, entry, client)

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_status_changed", lambda e: events.append(e)
    )

    client.async_get_parcels.return_value = [_underway()]
    await coordinator._async_update_data()  # first refresh: suppressed
    client.async_get_parcels.return_value = [_collectable()]
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["old_status"] == ParcelStatus.IN_TRANSIT
    assert events[0].data["new_status"] == ParcelStatus.AT_PICKUP_POINT


async def test_delivery_fires_delivered_event_and_not_status_changed(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = _coordinator(hass, entry, client)

    delivered = []
    changed = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_delivered", lambda e: delivered.append(e))
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_status_changed", lambda e: changed.append(e)
    )

    client.async_get_parcels.return_value = [active_parcel()]
    await coordinator._async_update_data()
    client.async_get_parcels.return_value = [
        archived_delivered_parcel(active_parcel()["parcelNumber"])
    ]
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert changed == []
    assert len(delivered) == 1
    assert delivered[0].data["status"] == ParcelStatus.DELIVERED


async def test_no_events_for_parcel_first_seen_delivered(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = _coordinator(hass, entry, client)

    fired = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_registered", lambda e: fired.append(e))
    hass.bus.async_listen(f"{DOMAIN}_parcel_delivered", lambda e: fired.append(e))

    client.async_get_parcels.return_value = [active_parcel()]
    await coordinator._async_update_data()  # first refresh seeds the state
    client.async_get_parcels.return_value = [active_parcel(), archived_delivered_parcel()]
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert fired == []


async def test_fires_registered_event_for_new_parcel(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = _coordinator(hass, entry, client)

    events = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_registered", lambda e: events.append(e))

    client.async_get_parcels.return_value = [active_parcel()]
    await coordinator._async_update_data()  # first refresh: suppressed
    client.async_get_parcels.return_value = [
        active_parcel(),
        active_parcel("PB000000001NO"),
    ]
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["barcode"] == "PB000000001NO"


async def test_fires_delivery_time_changed_event(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = _coordinator(hass, entry, client)

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_delivery_time_changed", lambda e: events.append(e)
    )

    client.async_get_parcels.return_value = [active_parcel()]
    await coordinator._async_update_data()  # first refresh: suppressed

    moved = active_parcel()
    moved["delivery"]["deliveryTime"]["deliveryWindow"] = {
        "start": "2026-04-29T16:00:00Z",
        "end": "2026-04-29T18:00:00Z",
    }
    client.async_get_parcels.return_value = [moved]
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["new_planned_from"] == "2026-04-29T16:00:00Z"


async def test_losing_the_eta_is_silent(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = _coordinator(hass, entry, client)

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_delivery_time_changed", lambda e: events.append(e)
    )

    client.async_get_parcels.return_value = [active_parcel()]
    await coordinator._async_update_data()

    dropped = active_parcel()
    dropped["delivery"] = {}
    client.async_get_parcels.return_value = [dropped]
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert events == []
