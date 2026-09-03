"""Posten Bring OAuth session and account-inbox API client.

Two responsibilities, kept apart:

* :class:`PostenBringSession` owns the whole token lifecycle — building the
  one-time browser authorization URL, exchanging the pasted-back redirect for
  tokens, and refreshing before expiry. Every token-endpoint call is
  Basic-authenticated with the brand's embedded ``client_id``/``client_secret``
  pair (const.py's ``BRANDS``) — the live gate confirmed this client is not
  public and PKCE does not change that, so this module never builds or sends
  a ``code_verifier``.
* :class:`PostenBringApiClient` owns the read-only inbox call: a JSON ``POST``
  with a bearer token, paging while ``remainingCount > 0`` and deduplicating
  by ``parcelNumber``.

Both raise :class:`~.const.PostenBringAuthError` only when the credential
itself was rejected (a bad/expired refresh token, or the app client itself
being retired) — never for a transient outage, so the caller does not push
users into a reauth flow they cannot complete.
"""
from __future__ import annotations

import base64
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp

from .const import (
    AUTHORIZE_PATH,
    BRANDS,
    DEFAULT_TOKEN_LIFETIME_SECONDS,
    INBOX_LOOKBACK_DAYS,
    INBOX_MAX_PAGES,
    INBOX_URL,
    REQUEST_TIMEOUT_SECONDS,
    TOKEN_HEADERS,
    TOKEN_PATH,
    TOKEN_REFRESH_MARGIN_SECONDS,
    PostenBringApiError,
    PostenBringAuthError,
)

_LOGGER = logging.getLogger(__name__)

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
_TOKEN_REFRESH_MARGIN = timedelta(seconds=TOKEN_REFRESH_MARGIN_SECONDS)


def generate_state() -> str:
    """Return a fresh, unguessable ``state`` value for one authorization request."""
    return secrets.token_urlsafe(24)


def build_authorization_url(brand: str, state: str, *, language: str = "en") -> str:
    """Build the one-time browser authorization URL for ``brand``.

    No ``code_challenge``/``code_verifier``: the live gate (posten-bring.md,
    2026-09-01) confirmed the token endpoint rejects a public-client request
    regardless of PKCE, and the official app never sends one.
    """
    config = BRANDS[brand]
    params = {
        "response_type": "code",
        "client_id": config["client_id"],
        "redirect_uri": config["redirect_uri"],
        "state": state,
        "lang": language,
        "is_app": "true",
        "brand": brand,
    }
    return f"{config['host']}{AUTHORIZE_PATH}?{urlencode(params)}"


def parse_redirect_url(
    value: str, *, expected_uri: str
) -> tuple[str | None, str | None]:
    """Pull ``(code, state)`` out of a pasted ``posten://…``/``bring://…`` URL.

    Users paste messily — leading/trailing whitespace, a trailing newline —
    so this strips first. Returns ``(None, None)`` if the redirect does not
    even match this brand's scheme, so the caller can reject it before
    touching ``state`` at all.
    """
    parsed = urlparse(value.strip())
    if f"{parsed.scheme}://{parsed.netloc}{parsed.path}" != expected_uri:
        return None, None
    query = parse_qs(parsed.query)
    code = query.get("code", [None])[0]
    state = query.get("state", [None])[0]
    return code, state


class PostenBringSession:
    """Owns one config entry's OAuth tokens for one brand: exchange, refresh.

    One instance per config entry. A fresh instance always starts with no
    cached access token, so its first :meth:`async_get_access_token` call
    refreshes even though the refresh token itself may not be new.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        brand: str,
        refresh_token: str | None = None,
    ) -> None:
        """Initialise with an optional already-persisted refresh token."""
        self._session = session
        self._brand = brand
        self.refresh_token = refresh_token
        self._access_token: str | None = None
        self._expires_at: datetime | None = None
        # Set when a refresh response carries a *different* refresh token
        # than the one sent — persisted by the coordinator/config flow via
        # pop_refresh_token_changed().
        self._refresh_token_changed = False

    @property
    def brand(self) -> str:
        """The brand (``posten``/``bring``) this session authenticates as."""
        return self._brand

    @property
    def access_token(self) -> str | None:
        """The most recently cached access token, or ``None`` before first use."""
        return self._access_token

    @property
    def _needs_refresh(self) -> bool:
        if self._access_token is None or self._expires_at is None:
            return True
        return datetime.now(timezone.utc) >= self._expires_at - _TOKEN_REFRESH_MARGIN

    def pop_refresh_token_changed(self) -> bool:
        """Return whether the refresh token rotated, and clear the flag."""
        value = self._refresh_token_changed
        self._refresh_token_changed = False
        return value

    def _basic_auth_header(self) -> str:
        """Build the ``Authorization: Basic …`` header value for this brand.

        Built by hand (rather than aiohttp's ``BasicAuth``/``auth=`` kwarg,
        both deprecated for removal in aiohttp 4.0) from the brand's own
        embedded credential pair.
        """
        config = BRANDS[self._brand]
        raw = f"{config['client_id']}:{config['client_secret']}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    async def async_exchange_code(self, code: str) -> dict[str, Any]:
        """Exchange a single-use authorization code for tokens.

        The caller must discard ``code`` immediately after this call,
        whether it succeeds or fails — it cannot be reused.
        """
        config = BRANDS[self._brand]
        payload = await self._async_post_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config["redirect_uri"],
                "response_type": "code",
            }
        )
        self._store_tokens(payload)
        refresh_token = payload.get("refresh_token")
        if not refresh_token:
            raise PostenBringApiError(
                "token response had no refresh_token"
            )
        self.refresh_token = refresh_token
        return payload

    async def async_get_access_token(self) -> str:
        """Return a valid access token, refreshing first if it is near expiry."""
        if self.refresh_token is None:
            raise PostenBringApiError(
                "no refresh_token to refresh with — the config entry is not set up"
            )
        if self._needs_refresh:
            await self._async_refresh()
        assert self._access_token is not None
        return self._access_token

    async def async_handle_unauthorized(self) -> str:
        """Force exactly one refresh after a 401/403 from the inbox.

        Callers must retry the failing request once with the returned token
        and then give up — never loop.
        """
        if self.refresh_token is None:
            raise PostenBringApiError(
                "no refresh_token to refresh with — the config entry is not set up"
            )
        await self._async_refresh()
        assert self._access_token is not None
        return self._access_token

    async def _async_refresh(self) -> None:
        payload = await self._async_post_token(
            {"grant_type": "refresh_token", "refresh_token": self.refresh_token}
        )
        self._store_tokens(payload)
        new_refresh_token = payload.get("refresh_token")
        if new_refresh_token and new_refresh_token != self.refresh_token:
            self.refresh_token = new_refresh_token
            self._refresh_token_changed = True

    async def _async_post_token(self, body: dict[str, str]) -> dict[str, Any]:
        config = BRANDS[self._brand]
        url = f"{config['host']}{TOKEN_PATH}"
        async with self._session.post(
            url,
            data=body,
            headers={**TOKEN_HEADERS, "Authorization": self._basic_auth_header()},
            timeout=_REQUEST_TIMEOUT,
        ) as response:
            text = await response.text()
            try:
                payload = json.loads(text) if text else {}
            except ValueError:
                payload = {}
            if (
                response.status == 200
                and isinstance(payload, dict)
                and payload.get("access_token")
            ):
                return payload
            error = payload.get("error") if isinstance(payload, dict) else None
            if response.status in (400, 401, 403) and error in (
                "invalid_grant",
                "invalid_client",
                "unauthorized_client",
            ):
                raise PostenBringAuthError(
                    f"Posten Bring rejected the token request ({error or response.status})",
                    status_code=response.status,
                )
            raise PostenBringApiError(
                f"token endpoint returned HTTP {response.status}",
                status_code=response.status,
            )

    def _store_tokens(self, payload: dict[str, Any]) -> None:
        access_token = payload.get("access_token")
        if not access_token:
            raise PostenBringApiError("token response had no access_token")
        self._access_token = access_token
        expires_in = payload.get("expires_in")
        try:
            lifetime = timedelta(seconds=float(expires_in))
        except (TypeError, ValueError):
            lifetime = timedelta(seconds=DEFAULT_TOKEN_LIFETIME_SECONDS)
        self._expires_at = datetime.now(timezone.utc) + lifetime


class PostenBringApiClient:
    """Read-only client for the shared ``api.posten.no`` account inbox."""

    def __init__(
        self, session: aiohttp.ClientSession, oauth_session: PostenBringSession
    ) -> None:
        """Initialise the client with a shared session and its OAuth session."""
        self._session = session
        self._oauth = oauth_session

    async def async_get_parcels(self) -> list[dict[str, Any]]:
        """Return the account's parcels, paging the incremental-sync inbox call.

        Bounded on two axes, per the build plan: a rolling ``lastUpdated``
        window (``INBOX_LOOKBACK_DAYS``) rather than a full-account export,
        and a hard page cap (``INBOX_MAX_PAGES``) so a non-advancing
        ``remainingCount`` cursor cannot loop forever. Deduplicates by
        ``parcelNumber`` across pages.
        """
        since = (
            datetime.now(timezone.utc) - timedelta(days=INBOX_LOOKBACK_DAYS)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        collected: dict[str, dict[str, Any]] = {}
        exclude: list[str] = []
        for _ in range(INBOX_MAX_PAGES):
            envelope = await self._async_request({"lastUpdated": since, "exclude": exclude})
            parcels = envelope.get("parcels")
            page = [p for p in parcels if isinstance(p, dict)] if isinstance(parcels, list) else []

            new_numbers = []
            for parcel in page:
                number = parcel.get("parcelNumber")
                if not isinstance(number, str):
                    continue
                if number not in collected:
                    new_numbers.append(number)
                collected[number] = parcel

            remaining = envelope.get("remainingCount")
            if not isinstance(remaining, int) or remaining <= 0:
                break
            if not new_numbers:
                # The cursor did not advance — bail out rather than looping
                # on a response that never shrinks remainingCount.
                _LOGGER.warning(
                    "Posten Bring inbox page returned no new parcelNumbers "
                    "while remainingCount=%s — stopping pagination early",
                    remaining,
                )
                break
            exclude.extend(new_numbers)
        else:
            _LOGGER.warning(
                "Posten Bring inbox pagination hit the %d-page cap — some "
                "parcels may be missing this poll",
                INBOX_MAX_PAGES,
            )

        return list(collected.values())

    async def _async_request(self, body: dict[str, Any]) -> dict[str, Any]:
        """One inbox POST, refreshing the access token once on a 401/403."""
        access_token = await self._oauth.async_get_access_token()
        payload, status, retry_after = await self._async_do_request(access_token, body)
        if status in (401, 403):
            access_token = await self._oauth.async_handle_unauthorized()
            payload, status, retry_after = await self._async_do_request(access_token, body)

        if status in (401, 403):
            raise PostenBringAuthError(f"HTTP {status}", status_code=status)
        if status == 429:
            raise PostenBringApiError(
                "HTTP 429", status_code=429, retry_after=retry_after
            )
        if status != 200:
            raise PostenBringApiError(f"HTTP {status}", status_code=status)
        if not isinstance(payload, dict):
            raise PostenBringApiError("unexpected body (not a JSON object)")
        return payload

    async def _async_do_request(
        self, access_token: str, body: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, int, float | None]:
        async with self._session.post(
            INBOX_URL,
            json=body,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=_REQUEST_TIMEOUT,
        ) as response:
            try:
                payload = await response.json(content_type=None)
            except ValueError:
                payload = None
            retry_after_header = response.headers.get("Retry-After")
            try:
                retry_after = float(retry_after_header) if retry_after_header else None
            except ValueError:
                retry_after = None  # an HTTP-date, not seconds; let the caller's own backoff handle it
            return (
                payload if isinstance(payload, dict) else None,
                response.status,
                retry_after,
            )


__all__ = [
    "PostenBringApiClient",
    "PostenBringApiError",
    "PostenBringAuthError",
    "PostenBringSession",
    "build_authorization_url",
    "generate_state",
    "parse_redirect_url",
]
