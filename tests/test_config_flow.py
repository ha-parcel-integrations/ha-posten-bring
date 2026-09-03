"""Tests for the Posten Bring config and options flow."""
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_USER
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.posten_bring.api import PostenBringApiError, PostenBringAuthError
from custom_components.posten_bring.const import (
    BRANDS,
    CONF_BRAND,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)

SESSION_CLASS = "custom_components.posten_bring.config_flow.PostenBringSession"


def _fake_session(
    *, refresh_token: str = "the-refresh-token", exchange_side_effect=None
) -> MagicMock:
    session = MagicMock()
    session.async_exchange_code = AsyncMock(side_effect=exchange_side_effect)
    session.refresh_token = refresh_token
    return session


def _entry(brand: str = "bring") -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=BRANDS[brand]["name"],
        data={CONF_BRAND: brand, CONF_REFRESH_TOKEN: "RT-OLD"},
        options={
            CONF_DELIVERED_FILTER_TYPE: "days",
            CONF_DELIVERED_FILTER_AMOUNT: 7,
            CONF_INCLUDE_HISTORY: False,
        },
    )


def _state_from(result: dict) -> str:
    url = result["description_placeholders"]["authorize_url"]
    return url.split("state=")[1].split("&")[0]


async def _start_bring_flow(hass, *, brand: str = "bring"):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BRAND: brand}
    )


# ---------------------------------------------------------------------------
# user step — brand picker, then browser-paste login
# ---------------------------------------------------------------------------


async def test_user_flow_shows_brand_picker(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    assert result["type"] == "form"


async def test_user_flow_shows_authorize_url_for_the_picked_brand(hass):
    with patch(SESSION_CLASS, return_value=_fake_session()):
        result = await _start_bring_flow(hass, brand="posten")

    assert result["step_id"] == "login"
    assert result["description_placeholders"]["authorize_url"].startswith(
        BRANDS["posten"]["host"]
    )
    # strings.json's "login" step title/description reference {brand} —
    # a missing key here throws formatjs MISSING_VALUE in the frontend.
    assert result["description_placeholders"]["brand"] == BRANDS["posten"]["name"]


async def test_user_flow_creates_entry_on_valid_redirect(hass):
    session = _fake_session(refresh_token="RT1")
    with patch(SESSION_CLASS, return_value=session):
        result = await _start_bring_flow(hass, brand="bring")
        state = _state_from(result)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"redirect_url": f"bring://login?code=the-code&state={state}"},
        )

    assert result["type"] == "create_entry"
    assert result["title"] == "Bring"
    assert result["data"][CONF_BRAND] == "bring"
    assert result["data"][CONF_REFRESH_TOKEN] == "RT1"
    session.async_exchange_code.assert_awaited_once_with("the-code")


async def test_user_flow_pasted_url_with_whitespace(hass):
    """Users paste messily — leading/trailing whitespace, a trailing newline."""
    session = _fake_session()
    with patch(SESSION_CLASS, return_value=session):
        result = await _start_bring_flow(hass, brand="bring")
        state = _state_from(result)
        messy = f"  bring://login?code=the-code&state={state} \n"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"redirect_url": messy}
        )

    assert result["type"] == "create_entry"


async def test_login_step_rejects_missing_code(hass):
    with patch(SESSION_CLASS, return_value=_fake_session()):
        result = await _start_bring_flow(hass, brand="bring")
        state = _state_from(result)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"redirect_url": f"bring://login?state={state}"}
        )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_redirect"}


async def test_login_step_rejects_mismatched_state(hass):
    with patch(SESSION_CLASS, return_value=_fake_session()):
        result = await _start_bring_flow(hass, brand="bring")
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"redirect_url": "bring://login?code=abc&state=totally-wrong"},
        )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_redirect"}


async def test_login_step_rejects_wrong_scheme(hass):
    with patch(SESSION_CLASS, return_value=_fake_session()):
        result = await _start_bring_flow(hass, brand="bring")
        state = _state_from(result)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"redirect_url": f"posten://login?code=abc&state={state}"},
        )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_redirect"}


@pytest.mark.parametrize(
    "error,expected",
    [
        (PostenBringAuthError("HTTP 401"), "invalid_auth"),
        (PostenBringApiError("HTTP 500"), "cannot_connect"),
        (aiohttp.ClientError("boom"), "cannot_connect"),
    ],
)
async def test_login_step_surfaces_errors(hass, error, expected):
    session = _fake_session(exchange_side_effect=error)
    with patch(SESSION_CLASS, return_value=session):
        result = await _start_bring_flow(hass, brand="bring")
        state = _state_from(result)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"redirect_url": f"bring://login?code=the-code&state={state}"},
        )

    assert result["type"] == "form"
    assert result["errors"] == {"base": expected}


# ---------------------------------------------------------------------------
# reauth
# ---------------------------------------------------------------------------


async def test_reauth_updates_the_refresh_token(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    session = _fake_session(refresh_token="RT-NEW")

    with patch(SESSION_CLASS, return_value=session):
        result = await entry.start_reauth_flow(hass)
        assert result["step_id"] == "reauth_confirm"
        state = _state_from(result)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"redirect_url": f"bring://login?code=the-code&state={state}"},
        )
        await hass.async_block_till_done()

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_REFRESH_TOKEN] == "RT-NEW"


async def test_reauth_does_not_ask_for_the_brand_again(hass):
    entry = _entry(brand="posten")
    entry.add_to_hass(hass)

    with patch(SESSION_CLASS, return_value=_fake_session()):
        result = await entry.start_reauth_flow(hass)

    assert result["step_id"] == "reauth_confirm"
    assert BRANDS["posten"]["host"] in result["description_placeholders"]["authorize_url"]


async def test_reauth_surfaces_invalid_credentials(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    session = _fake_session(exchange_side_effect=PostenBringAuthError("HTTP 401"))

    with patch(SESSION_CLASS, return_value=session):
        result = await entry.start_reauth_flow(hass)
        state = _state_from(result)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"redirect_url": f"bring://login?code=the-code&state={state}"},
        )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}


# ---------------------------------------------------------------------------
# options
# ---------------------------------------------------------------------------


async def test_options_flow_saves_and_reloads(hass):
    entry = _entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"

    with patch.object(
        hass.config_entries, "async_schedule_reload"
    ) as schedule_reload:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "delivered": {
                    CONF_DELIVERED_FILTER_TYPE: "parcels",
                    CONF_DELIVERED_FILTER_AMOUNT: 5,
                },
                "history": {CONF_INCLUDE_HISTORY: True},
            },
        )

    assert result["type"] == "create_entry"
    assert result["data"] == {
        CONF_DELIVERED_FILTER_TYPE: "parcels",
        CONF_DELIVERED_FILTER_AMOUNT: 5,
        CONF_INCLUDE_HISTORY: True,
    }
    schedule_reload.assert_called_once_with(entry.entry_id)
