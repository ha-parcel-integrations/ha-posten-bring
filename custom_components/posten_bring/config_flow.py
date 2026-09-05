"""Config flow for the Posten Bring parcel tracker integration.

Posten Bring's login is a one-time browser hop, not a form the integration
can submit for the user: the flow builds its own authorization URL with a
freshly generated ``state``, the user opens it, logs in (Vipps or phone
number), and pastes back the ``posten://…``/``bring://…`` redirect the
browser could not open. The stored credential is the refresh token that
comes out of the code exchange — never a password, never the authorization
code itself, which is discarded the moment the exchange finishes.

No PKCE: a live gate (2026-09-01) confirmed the token endpoint rejects a
public-client request regardless of PKCE, and the official app never sends a
``code_verifier`` —
every token request is Basic-authenticated with the brand's embedded
``client_id``/``client_secret`` instead (see const.py's ``BRANDS``).

No stable account identifier is available from this API to key a
``unique_id`` on: the token response carries only opaque
``access_token``/``refresh_token``/``expires_in`` fields (no ID token, no
profile call — the build plan explicitly rules out any endpoint beyond the
inbox). This flow therefore does not call ``async_set_unique_id`` /
``_abort_if_unique_id_configured`` for new entries — a deliberate,
documented gap, not an oversight (see CLAUDE.md).
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    PostenBringApiError,
    PostenBringAuthError,
    PostenBringSession,
    build_authorization_url,
    generate_state,
    parse_redirect_url,
)
from .const import (
    BRANDS,
    CONF_BRAND,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_REFRESH_TOKEN,
    DEFAULT_BRAND,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DEFAULT_INCLUDE_HISTORY,
    DOMAIN,
    REDIRECT_URL_DOCS_URL,
)

_LOGGER = logging.getLogger(__name__)

_REDIRECT_SCHEMA = vol.Schema({vol.Required("redirect_url"): str})

_BRAND_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=list(BRANDS),
        translation_key=CONF_BRAND,
        mode=selector.SelectSelectorMode.LIST,
    )
)
_BRAND_SCHEMA = vol.Schema(
    {vol.Required(CONF_BRAND, default=DEFAULT_BRAND): _BRAND_SELECTOR}
)


class PostenBringConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the browser-paste OAuth flow for the Posten Bring integration."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise per-flow state — never persisted, never reused."""
        self._brand: str | None = None
        self._oauth_session: PostenBringSession | None = None
        self._authorize_url: str | None = None
        self._state: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> PostenBringOptionsFlowHandler:
        """Return the options flow handler."""
        return PostenBringOptionsFlowHandler()

    def _get_oauth_session(self) -> PostenBringSession:
        """Return this flow's one OAuth session, creating it on first use."""
        if self._oauth_session is None:
            assert self._brand is not None
            self._oauth_session = PostenBringSession(
                async_get_clientsession(self.hass), brand=self._brand
            )
        return self._oauth_session

    def _ensure_authorize_url(self) -> None:
        """Build the authorization URL once per flow.

        ``state`` is generated exactly once per flow and held for its
        lifetime — regenerating it on a retry would invalidate a URL the
        user may have already opened.
        """
        if self._authorize_url is not None:
            return
        assert self._brand is not None
        self._state = generate_state()
        self._authorize_url = build_authorization_url(self._brand, self._state)

    async def _async_exchange(self, redirect_url: str) -> str | None:
        """Parse + exchange the pasted redirect URL; return an error code or ``None``.

        On success the OAuth session now holds ``refresh_token`` and the
        exchanged tokens; the caller reads them straight back off it.
        """
        assert self._brand is not None and self._state is not None
        expected_uri = BRANDS[self._brand]["redirect_uri"]
        code, state = parse_redirect_url(redirect_url, expected_uri=expected_uri)
        if not code or not state or state != self._state:
            return "invalid_redirect"
        try:
            await self._get_oauth_session().async_exchange_code(code)
        except PostenBringAuthError:
            return "invalid_auth"
        except (PostenBringApiError, aiohttp.ClientError, TimeoutError):
            _LOGGER.debug("Failed to exchange the pasted redirect URL", exc_info=True)
            return "cannot_connect"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which brand (Posten/Bring) to sign in as, then show the login step."""
        if user_input is not None:
            self._brand = user_input[CONF_BRAND]
            return await self.async_step_login()

        return self.async_show_form(step_id="user", data_schema=_BRAND_SCHEMA)

    async def async_step_login(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the authorization URL and the paste-back form."""
        self._ensure_authorize_url()

        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._async_exchange(user_input["redirect_url"])
            if error is not None:
                errors["base"] = error
            else:
                session = self._get_oauth_session()
                return self.async_create_entry(
                    title=BRANDS[self._brand]["name"],
                    data={
                        CONF_BRAND: self._brand,
                        CONF_REFRESH_TOKEN: session.refresh_token,
                    },
                    options={
                        CONF_DELIVERED_FILTER_TYPE: DEFAULT_DELIVERED_FILTER_TYPE,
                        CONF_DELIVERED_FILTER_AMOUNT: DEFAULT_DELIVERED_FILTER_AMOUNT,
                        CONF_INCLUDE_HISTORY: DEFAULT_INCLUDE_HISTORY,
                    },
                )

        return self.async_show_form(
            step_id="login",
            data_schema=_REDIRECT_SCHEMA,
            errors=errors,
            description_placeholders={
                "brand": BRANDS[self._brand]["name"],
                "authorize_url": self._authorize_url or "",
                "docs_url": REDIRECT_URL_DOCS_URL,
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth after the refresh token stopped working.

        The entry already carries its brand (set at creation) — reauth
        never needs to ask again.
        """
        self._brand = entry_data[CONF_BRAND]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to repeat the browser paste and update the entry."""
        self._ensure_authorize_url()

        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._async_exchange(user_input["redirect_url"])
            if error is not None:
                errors["base"] = error
            else:
                session = self._get_oauth_session()
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_REFRESH_TOKEN: session.refresh_token},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_REDIRECT_SCHEMA,
            errors=errors,
            description_placeholders={
                "authorize_url": self._authorize_url or "",
                "docs_url": REDIRECT_URL_DOCS_URL,
            },
        )


class PostenBringOptionsFlowHandler(OptionsFlow):
    """Manage delivered retention and history in one sectioned form."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and handle the single sectioned options form."""
        if user_input is not None:
            delivered = user_input["delivered"]
            history = user_input["history"]
            # No update listener is registered — combining one with a
            # reload-on-update flow is deprecated.
            self.hass.config_entries.async_schedule_reload(
                self.config_entry.entry_id
            )
            return self.async_create_entry(
                title="",
                data={
                    CONF_DELIVERED_FILTER_TYPE: delivered[CONF_DELIVERED_FILTER_TYPE],
                    CONF_DELIVERED_FILTER_AMOUNT: int(
                        delivered[CONF_DELIVERED_FILTER_AMOUNT]
                    ),
                    CONF_INCLUDE_HISTORY: bool(history[CONF_INCLUDE_HISTORY]),
                },
            )

        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required("delivered"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_DELIVERED_FILTER_TYPE,
                                default=current.get(
                                    CONF_DELIVERED_FILTER_TYPE,
                                    DEFAULT_DELIVERED_FILTER_TYPE,
                                ),
                            ): selector.SelectSelector(
                                selector.SelectSelectorConfig(
                                    options=["days", "parcels"],
                                    translation_key=CONF_DELIVERED_FILTER_TYPE,
                                    mode=selector.SelectSelectorMode.LIST,
                                )
                            ),
                            vol.Required(
                                CONF_DELIVERED_FILTER_AMOUNT,
                                default=current.get(
                                    CONF_DELIVERED_FILTER_AMOUNT,
                                    DEFAULT_DELIVERED_FILTER_AMOUNT,
                                ),
                            ): selector.NumberSelector(
                                selector.NumberSelectorConfig(
                                    min=1,
                                    max=365,
                                    step=1,
                                    mode=selector.NumberSelectorMode.BOX,
                                )
                            ),
                        }
                    ),
                    {"collapsed": False},
                ),
                vol.Required("history"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_INCLUDE_HISTORY,
                                default=current.get(
                                    CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY
                                ),
                            ): selector.BooleanSelector(),
                        }
                    ),
                    {"collapsed": True},
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
