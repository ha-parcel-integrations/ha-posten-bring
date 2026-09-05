"""Constants for the Posten Bring parcel tracker integration."""
from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "posten_bring"


class ParcelStatus(StrEnum):
    """Carrier-agnostic parcel status.

    **Do not extend or rename these members.** Every integration in the parcel
    suite publishes exactly this vocabulary on the ``status`` field of each
    normalised parcel, so cross-carrier automations and the aggregator can
    target ``status: out_for_delivery`` regardless of carrier. Listed in
    roughly the order a parcel moves through.
    """

    REGISTERED = "registered"               # Sender announced the parcel; not handed over yet
    IN_TRANSIT = "in_transit"               # In the carrier's network
    OUT_FOR_DELIVERY = "out_for_delivery"   # On a delivery vehicle today
    AT_PICKUP_POINT = "at_pickup_point"     # Ready to collect at a pickup location
    DELIVERED = "delivered"                 # Handed over
    RETURNING = "returning"                 # Failed delivery, going back to sender
    PROBLEM = "problem"                     # Carrier reports an exception/issue
    UNKNOWN = "unknown"                     # Raw status we have not mapped yet


PLATFORMS = [Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]

# Every optional key the parcel contract defines. CAPABILITIES below must be a
# subset of this.
KNOWN_CAPABILITIES = frozenset(
    {"weight", "dimensions", "delivery_window", "pickup_point", "url", "history"}
)

# Posten Bring's inbox reports weight/dimensions, a delivery window, a pickup
# point and a per-parcel event history. Reflected here so the docs site's
# comparison table stays honest — keep in sync with parcels.py.
CAPABILITIES = frozenset(
    {"weight", "dimensions", "delivery_window", "pickup_point", "url", "history"}
)

# ---------------------------------------------------------------------------
# OAuth — Postenid (id.posten.no / login.bring.com), Basic-authenticated
# client credentials. The maintainer has explicitly approved shipping this
# embedded client_id/client_secret pair for this carrier only — do not reuse
# this pattern for another carrier without an equivalent override.
# ---------------------------------------------------------------------------

AUTHORIZE_PATH = "/api/oauth/authorizations/new"
TOKEN_PATH = "/api/oauth/accesstoken"

BRANDS: dict[str, dict[str, str]] = {
    "posten": {
        "name": "Posten",
        "host": "https://id.posten.no",
        "client_id": "f0ad2360e9f64a0986faafe66a9731e5",
        "client_secret": "b814595805d5859ebe1d69abd2e65b0d8f4b786478bb31b5bfeefb5dc5dc1b1be8dae22d83d4195f",
        "redirect_uri": "posten://login",
    },
    "bring": {
        "name": "Bring",
        "host": "https://login.bring.com",
        "client_id": "ffb3cd07a5a043a5977b7e272a06bb8f",
        "client_secret": "0b00ac6b4c474c0ea087552341f10a1446592b94dada40cbbdba97aa84cfe5bb41cba49339582",
        "redirect_uri": "bring://login",
    },
}
DEFAULT_BRAND = "bring"

# Docs page walking the user through copying the pasted-back redirect URL out
# of their browser's devtools/history for a custom scheme it cannot open.
REDIRECT_URL_DOCS_URL = (
    "https://github.com/ha-parcel-integrations/ha-posten-bring/blob/main/docs/"
    "finding-the-redirect-url.md"
)

# Requests to the token endpoint must set some header set or the request is
# rejected outright by the gateway (mirrors the DHL DE precedent) — kept as a
# constant so it stays in one place if it ever needs adjusting.
TOKEN_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded",
}

# aiohttp's own default (300s total) is too long to sit silently on a stalled
# identity-provider connection.
REQUEST_TIMEOUT_SECONDS = 30

# Refresh this long before the token's absolute expiry.
TOKEN_REFRESH_MARGIN_SECONDS = 300

# Fallback lifetime when a token response carries no `expires_in` at all.
DEFAULT_TOKEN_LIFETIME_SECONDS = 3600

# How many consecutive polls may be refused by the inbox with 401/403 — each
# one *after* a token refresh the identity provider accepted — before we stop
# calling it a server-side problem and ask the user to sign in again. Anything
# below this is retried, because the browser-paste reauth is expensive enough
# that a transient 403 must not trigger it.
MAX_UNAUTHORIZED_POLLS = 3

# ---------------------------------------------------------------------------
# Inbox — POST api.posten.no/parcel-api/v1/parcel, bearer auth, incremental
# sync (lastUpdated + exclude, page while remainingCount > 0).
# ---------------------------------------------------------------------------

INBOX_URL = "https://api.posten.no/parcel-api/v1/parcel"

# Public, keyless human-facing tracking page — used for the parcel's `url`
# field. Read-only, never called by this integration itself.
PUBLIC_TRACKING_URL = "https://sporing.posten.no/sporing/{barcode}"

# A bounded rolling window, not a full-account export ("Do not build" in the
# build plan) — how far back `lastUpdated` reaches on every poll.
INBOX_LOOKBACK_DAYS = 30

# Safety valve against a non-advancing `remainingCount` cursor looping
# forever.
INBOX_MAX_PAGES = 25

# Target market: Bring's three active consumer countries. Bring is no longer
# active in Finland — do not add "FI" here or advertise FI support anywhere.
SUPPORTED_COUNTRIES = frozenset({"NO", "SE", "DK"})


class PostenBringApiError(Exception):
    """Raised when a Posten Bring API call fails for a non-auth reason."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the detail and, where available, the HTTP status/Retry-After."""
        super().__init__(f"Posten Bring API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class PostenBringAuthError(PostenBringApiError):
    """Raised when Posten Bring rejects the refresh token or the app client.

    Distinct from :class:`PostenBringApiError` on purpose: only this one may
    trigger Home Assistant's reauth flow.
    """


# Entry-data keys. The stored credential is a refresh token, never a
# password — config_flow.py exchanges the pasted redirect URL for it once
# and discards the authorization code immediately after.
CONF_BRAND = "brand"
CONF_REFRESH_TOKEN = "refresh_token"

# Delivered-parcels retention: keep delivered parcels visible for the last N
# days, or keep only the N most recent — identical across the suite.
CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7

# Dynamic, status-driven polling — unconditional across the suite, no
# user-facing interval option.
QUIET_WINDOW_START_HOUR = 0
QUIET_WINDOW_END_HOUR = 6
HOT_INTERVAL_MINUTES = 15
MID_INTERVAL_MINUTES = 45
HOT_LOOKAHEAD_HOURS = 1
STAGGER_MINUTES = 7

# Per-parcel status history is opt-in and off by default, identical across
# the suite.
CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False

# Cap each parcel's history to the most recent N events.
HISTORY_MAX_EVENTS = 20

# Where users report a status/shape we do not map yet.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-posten-bring/issues/new"
    "?template=unrecognised_status.yml"
)
