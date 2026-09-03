"""Posten Bring parcel tracker custom component for Home Assistant."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PostenBringApiClient, PostenBringSession
from .const import CONF_BRAND, CONF_REFRESH_TOKEN, PLATFORMS
from .coordinator import PostenBringCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class PostenBringData:
    """Runtime data attached to a Posten Bring config entry."""

    client: PostenBringApiClient
    coordinator: PostenBringCoordinator
    oauth_session: PostenBringSession
    session: aiohttp.ClientSession


type PostenBringConfigEntry = ConfigEntry[PostenBringData]


async def async_setup_entry(
    hass: HomeAssistant, entry: PostenBringConfigEntry
) -> bool:
    """Set up Posten Bring from a config entry."""
    # Each config entry gets its own session so a token refresh mid-poll on
    # one account never touches another's in-flight request.
    session = aiohttp.ClientSession(
        connector=async_get_clientsession(hass).connector, connector_owner=False
    )
    oauth_session = PostenBringSession(
        session,
        brand=entry.data[CONF_BRAND],
        refresh_token=entry.data[CONF_REFRESH_TOKEN],
    )
    client = PostenBringApiClient(session, oauth_session)
    coordinator = PostenBringCoordinator(hass, client, entry, oauth_session=oauth_session)

    try:
        # Fetch initial data here, before forwarding to platforms. Raising
        # ConfigEntryNotReady/ConfigEntryAuthFailed from a forwarded platform
        # is too late for HA to catch cleanly (it logs a warning and
        # half-sets-up the entry); doing the first refresh here lets a
        # transient failure — or a rejected refresh token, surfaced by the
        # coordinator as ConfigEntryAuthFailed — fail the whole entry so HA
        # retries it (or starts reauth) cleanly.
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        # Without this, every setup retry leaks a session.
        await session.close()
        raise

    entry.runtime_data = PostenBringData(
        client=client, coordinator=coordinator, oauth_session=oauth_session, session=session
    )

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await session.close()
        raise

    # No entry.add_update_listener: the options flow calls
    # async_schedule_reload itself. Combining an update listener with a
    # reload-on-update flow is deprecated and becomes an error in HA 2026.12+.
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: PostenBringConfigEntry
) -> bool:
    """Unload a Posten Bring config entry."""
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.session.close()
        return True
    return False
