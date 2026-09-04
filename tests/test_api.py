"""Tests for the Posten Bring OAuth session and inbox API client."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.posten_bring.api import (
    PostenBringApiClient,
    PostenBringApiError,
    PostenBringAuthError,
    PostenBringSession,
    build_authorization_url,
    parse_redirect_url,
)
from custom_components.posten_bring.const import BRANDS

from .payloads import active_parcel, inbox_page, outgoing_parcel


def _session_returning(*responses: tuple[int, object]) -> MagicMock:
    """A MagicMock aiohttp session whose .post() yields each response in turn.

    Each item is ``(status, body)`` or ``(status, body, headers)``.
    """
    contexts = []
    for item in responses:
        status, body = item[0], item[1]
        headers = item[2] if len(item) > 2 else {}
        response = AsyncMock()
        response.status = status
        response.headers = headers
        response.text = AsyncMock(
            return_value=body if isinstance(body, str) else json.dumps(body)
        )
        if isinstance(body, str):
            response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
        else:
            response.json = AsyncMock(return_value=body)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=response)
        ctx.__aexit__ = AsyncMock(return_value=False)
        contexts.append(ctx)

    session = MagicMock()
    session.post = MagicMock(side_effect=contexts if len(contexts) > 1 else None)
    if len(contexts) == 1:
        session.post = MagicMock(return_value=contexts[0])
    return session


# ---------------------------------------------------------------------------
# URL construction / redirect parsing
# ---------------------------------------------------------------------------


def test_build_authorization_url_uses_the_brands_own_client():
    url = build_authorization_url("bring", "the-state")
    assert url.startswith(BRANDS["bring"]["host"])
    assert f"client_id={BRANDS['bring']['client_id']}" in url
    assert "state=the-state" in url
    assert "code_challenge" not in url
    assert "code_verifier" not in url


def test_parse_redirect_url_extracts_code_and_state():
    code, state = parse_redirect_url(
        "bring://login?code=abc123&state=xyz", expected_uri="bring://login"
    )
    assert code == "abc123"
    assert state == "xyz"


def test_parse_redirect_url_strips_whitespace():
    code, state = parse_redirect_url(
        "  bring://login?code=abc123&state=xyz\n", expected_uri="bring://login"
    )
    assert code == "abc123"


def test_parse_redirect_url_rejects_wrong_scheme():
    code, state = parse_redirect_url(
        "posten://login?code=abc123&state=xyz", expected_uri="bring://login"
    )
    assert code is None
    assert state is None


def test_parse_redirect_url_handles_missing_query():
    code, state = parse_redirect_url("bring://login", expected_uri="bring://login")
    assert code is None
    assert state is None


# ---------------------------------------------------------------------------
# PostenBringSession — token exchange and refresh
# ---------------------------------------------------------------------------


async def test_exchange_code_sends_basic_auth_and_stores_tokens():
    session = _session_returning(
        (200, {"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600})
    )
    oauth = PostenBringSession(session, brand="bring")

    payload = await oauth.async_exchange_code("the-code")

    assert payload["access_token"] == "AT1"
    assert oauth.refresh_token == "RT1"
    import base64

    kwargs = session.post.call_args[1]
    expected = base64.b64encode(
        f"{BRANDS['bring']['client_id']}:{BRANDS['bring']['client_secret']}".encode()
    ).decode()
    assert kwargs["headers"]["Authorization"] == f"Basic {expected}"
    assert kwargs["data"]["grant_type"] == "authorization_code"
    assert kwargs["data"]["code"] == "the-code"


async def test_exchange_code_raises_api_error_without_refresh_token():
    session = _session_returning((200, {"access_token": "AT1", "expires_in": 3600}))
    oauth = PostenBringSession(session, brand="posten")
    with pytest.raises(PostenBringApiError):
        await oauth.async_exchange_code("the-code")


@pytest.mark.parametrize("status,error", [(401, "invalid_client"), (400, "invalid_grant")])
async def test_exchange_code_raises_auth_error_on_rejection(status, error):
    session = _session_returning((status, {"error": error}))
    oauth = PostenBringSession(session, brand="bring")
    with pytest.raises(PostenBringAuthError):
        await oauth.async_exchange_code("bad-code")


async def test_exchange_code_raises_api_error_on_outage():
    session = _session_returning((503, {}))
    oauth = PostenBringSession(session, brand="bring")
    with pytest.raises(PostenBringApiError) as err:
        await oauth.async_exchange_code("the-code")
    assert not isinstance(err.value, PostenBringAuthError)


async def test_get_access_token_refreshes_when_none_cached():
    session = _session_returning(
        (200, {"access_token": "AT2", "refresh_token": "RT1", "expires_in": 3600})
    )
    oauth = PostenBringSession(session, brand="bring", refresh_token="RT0")
    token = await oauth.async_get_access_token()
    assert token == "AT2"
    assert session.post.call_args[1]["data"]["grant_type"] == "refresh_token"


async def test_get_access_token_reuses_unexpired_token():
    session = _session_returning(
        (200, {"access_token": "AT2", "refresh_token": "RT1", "expires_in": 3600})
    )
    oauth = PostenBringSession(session, brand="bring", refresh_token="RT0")
    await oauth.async_get_access_token()
    session.post.reset_mock()
    token = await oauth.async_get_access_token()
    assert token == "AT2"
    session.post.assert_not_called()


async def test_get_access_token_without_refresh_token_raises():
    oauth = PostenBringSession(MagicMock(), brand="bring")
    with pytest.raises(PostenBringApiError):
        await oauth.async_get_access_token()


async def test_refresh_token_rotation_reaches_the_token_updater():
    session = _session_returning(
        (200, {"access_token": "AT2", "refresh_token": "RT-NEW", "expires_in": 3600})
    )
    updater = MagicMock()
    oauth = PostenBringSession(
        session, brand="bring", refresh_token="RT-OLD", token_updater=updater
    )
    await oauth.async_get_access_token()
    assert oauth.refresh_token == "RT-NEW"
    updater.assert_called_once_with("RT-NEW")


async def test_rotation_is_handed_over_before_the_call_it_was_refreshed_for():
    """The rotated token must be persisted even if everything after it fails.

    The server burns the token that was sent, so persisting only after a
    successful inbox call would leave the entry holding a dead credential
    whenever anything in between failed — and cost the user a full reauth on
    the next restart.
    """
    session = _session_returning(
        (200, {"access_token": "AT2", "refresh_token": "RT-NEW", "expires_in": 3600}),
        (500, {}),
    )
    updater = MagicMock()
    oauth = PostenBringSession(
        session, brand="bring", refresh_token="RT-OLD", token_updater=updater
    )
    client = PostenBringApiClient(session, oauth)

    with pytest.raises(PostenBringApiError):
        await client.async_get_parcels()

    updater.assert_called_once_with("RT-NEW")


async def test_refresh_without_rotation_does_not_call_the_token_updater():
    session = _session_returning(
        (200, {"access_token": "AT2", "refresh_token": "RT-OLD", "expires_in": 3600})
    )
    updater = MagicMock()
    oauth = PostenBringSession(
        session, brand="bring", refresh_token="RT-OLD", token_updater=updater
    )
    await oauth.async_get_access_token()
    updater.assert_not_called()


async def test_handle_unauthorized_forces_one_refresh():
    session = _session_returning(
        (200, {"access_token": "AT-NEW", "refresh_token": "RT1", "expires_in": 3600})
    )
    oauth = PostenBringSession(session, brand="bring", refresh_token="RT0")
    token = await oauth.async_handle_unauthorized()
    assert token == "AT-NEW"


async def test_handle_unauthorized_without_refresh_token_raises():
    oauth = PostenBringSession(MagicMock(), brand="bring")
    with pytest.raises(PostenBringApiError):
        await oauth.async_handle_unauthorized()


async def test_post_token_raises_on_malformed_body():
    session = _session_returning((200, "not json"))
    oauth = PostenBringSession(session, brand="bring")
    with pytest.raises(PostenBringApiError):
        await oauth.async_exchange_code("the-code")


# ---------------------------------------------------------------------------
# PostenBringApiClient — inbox pagination
# ---------------------------------------------------------------------------


def _oauth_stub(access_token: str = "AT1") -> MagicMock:
    oauth = MagicMock(spec=PostenBringSession)
    oauth.async_get_access_token = AsyncMock(return_value=access_token)
    oauth.async_handle_unauthorized = AsyncMock(return_value=access_token)
    return oauth


async def test_get_parcels_returns_a_single_page():
    session = _session_returning((200, inbox_page([active_parcel()])))
    client = PostenBringApiClient(session, _oauth_stub())
    parcels = await client.async_get_parcels()
    assert len(parcels) == 1
    assert parcels[0]["parcelNumber"] == active_parcel()["parcelNumber"]


async def test_get_parcels_pages_while_remaining_count_positive():
    page1 = inbox_page([active_parcel("A")], remaining_count=1)
    page2 = inbox_page([active_parcel("B")], remaining_count=0)
    session = _session_returning((200, page1), (200, page2))
    client = PostenBringApiClient(session, _oauth_stub())

    parcels = await client.async_get_parcels()

    assert {p["parcelNumber"] for p in parcels} == {"A", "B"}
    second_call_body = session.post.call_args_list[1][1]["json"]
    assert second_call_body["exclude"] == ["A"]


async def test_get_parcels_dedupes_by_parcel_number():
    page1 = inbox_page([active_parcel("A")], remaining_count=1)
    page2 = inbox_page([active_parcel("A"), active_parcel("B")], remaining_count=0)
    session = _session_returning((200, page1), (200, page2))
    client = PostenBringApiClient(session, _oauth_stub())

    parcels = await client.async_get_parcels()

    assert sorted(p["parcelNumber"] for p in parcels) == ["A", "B"]


async def test_get_parcels_stops_on_non_advancing_cursor():
    """A remainingCount that never shrinks (no new parcelNumbers) must not loop forever."""
    stuck_page = inbox_page([active_parcel("A")], remaining_count=5)
    session = _session_returning(*([(200, stuck_page)] * 30))
    client = PostenBringApiClient(session, _oauth_stub())

    parcels = await client.async_get_parcels()

    assert len(parcels) == 1
    assert session.post.call_count < 30


async def test_get_parcels_respects_the_page_cap(monkeypatch):
    import custom_components.posten_bring.api as api_module

    monkeypatch.setattr(api_module, "INBOX_MAX_PAGES", 3)
    pages = [
        inbox_page([active_parcel(f"N{i}")], remaining_count=1) for i in range(5)
    ]
    session = _session_returning(*[(200, page) for page in pages])
    client = PostenBringApiClient(session, _oauth_stub())

    parcels = await client.async_get_parcels()

    assert session.post.call_count == 3
    assert len(parcels) == 3


async def test_get_parcels_refreshes_once_on_401_then_succeeds():
    session = _session_returning((401, {}), (200, inbox_page([active_parcel()])))
    oauth = _oauth_stub()
    client = PostenBringApiClient(session, oauth)

    parcels = await client.async_get_parcels()

    assert len(parcels) == 1
    oauth.async_handle_unauthorized.assert_awaited_once()


@pytest.mark.parametrize("status", [401, 403])
async def test_inbox_refusing_a_freshly_refreshed_token_is_not_an_auth_error(status):
    """The refresh succeeded, so the credential is fine — the inbox is not.

    Raising an auth error here would push the user through the browser-paste
    reauth over a server-side refusal they cannot fix by signing in again.
    """
    session = _session_returning((status, {}), (status, {}))
    client = PostenBringApiClient(session, _oauth_stub())
    with pytest.raises(PostenBringApiError) as excinfo:
        await client.async_get_parcels()
    assert not isinstance(excinfo.value, PostenBringAuthError)
    assert excinfo.value.status_code == status


async def test_get_parcels_raises_on_429_with_retry_after_header():
    session = _session_returning((429, {}, {"Retry-After": "120"}))
    client = PostenBringApiClient(session, _oauth_stub())
    with pytest.raises(PostenBringApiError) as excinfo:
        await client.async_get_parcels()
    assert excinfo.value.status_code == 429
    assert excinfo.value.retry_after == 120.0


async def test_get_parcels_raises_on_429_without_retry_after_header():
    session = _session_returning((429, {}))
    client = PostenBringApiClient(session, _oauth_stub())
    with pytest.raises(PostenBringApiError) as excinfo:
        await client.async_get_parcels()
    assert excinfo.value.retry_after is None


async def test_get_parcels_raises_on_error_status():
    session = _session_returning((503, {}))
    client = PostenBringApiClient(session, _oauth_stub())
    with pytest.raises(PostenBringApiError):
        await client.async_get_parcels()


async def test_get_parcels_raises_on_malformed_json():
    session = _session_returning((200, "not json"))
    client = PostenBringApiClient(session, _oauth_stub())
    with pytest.raises(PostenBringApiError):
        await client.async_get_parcels()


async def test_get_parcels_treats_non_list_parcels_as_empty():
    session = _session_returning((200, {"parcels": "nope", "remainingCount": 0}))
    client = PostenBringApiClient(session, _oauth_stub())
    assert await client.async_get_parcels() == []


async def test_get_parcels_includes_outgoing_direction_untouched():
    session = _session_returning((200, inbox_page([outgoing_parcel()])))
    client = PostenBringApiClient(session, _oauth_stub())
    parcels = await client.async_get_parcels()
    assert parcels[0]["direction"] == "send"


async def test_get_parcels_propagates_network_error():
    session = MagicMock()
    session.post = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = PostenBringApiClient(session, _oauth_stub())
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcels()
