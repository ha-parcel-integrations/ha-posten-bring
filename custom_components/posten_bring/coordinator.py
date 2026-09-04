"""Coordinator for the Posten Bring parcel tracker integration.

Fetching, direction split and event firing. Posten Bring's inbox returns
incoming and outgoing parcels in the same list (``direction``), unlike
carriers with a separate sent-shipments endpoint — so a single coordinator
splits the one fetched list into incoming/outgoing rather than needing two.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import PostenBringApiClient, PostenBringApiError, PostenBringAuthError
from .const import (
    CONF_INCLUDE_HISTORY,
    DEFAULT_INCLUDE_HISTORY,
    DOMAIN,
    HOT_INTERVAL_MINUTES,
    HOT_LOOKAHEAD_HOURS,
    MAX_UNAUTHORIZED_POLLS,
    MID_INTERVAL_MINUTES,
    QUIET_WINDOW_END_HOUR,
    QUIET_WINDOW_START_HOUR,
    STAGGER_MINUTES,
    ParcelStatus,
)
from .parcels import (
    DIRECTION_OUTGOING,
    apply_delivered_filter,
    normalize_parcel,
    parcel_direction,
    sort_parcels_by_ts,
)

_LOGGER = logging.getLogger(__name__)

# Fallback backoff when a 429 carries no ``Retry-After`` of its own:
# ``BACKOFF_BASE_SECONDS * 2**consecutive_429``, capped at ``BACKOFF_CAP_SECONDS``.
BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 3600


def _stagger_minutes(entry_id: str) -> int:
    """Deterministic per-install offset, stable across restarts."""
    digest = hashlib.sha256(entry_id.encode()).hexdigest()
    return int(digest, 16) % STAGGER_MINUTES


def _in_quiet_window(moment: datetime) -> bool:
    """Whether ``moment`` (local time) falls in the no-polling window."""
    return QUIET_WINDOW_START_HOUR <= moment.hour < QUIET_WINDOW_END_HOUR


def _next_anchor(now: datetime) -> datetime:
    """Return the next of the two daily anchors (00:00 / 06:00 local)."""
    six_today = now.replace(
        hour=QUIET_WINDOW_END_HOUR, minute=0, second=0, microsecond=0
    )
    if now < six_today:
        return six_today
    midnight_tomorrow = (now + timedelta(days=1)).replace(
        hour=QUIET_WINDOW_START_HOUR, minute=0, second=0, microsecond=0
    )
    return midnight_tomorrow


def _hottest_tier_minutes(active_parcels: list[dict], now: datetime) -> int:
    """Tier for the account-based model.

    Never returns ``None`` — a single account call already returns the full
    state, so the mid-tier poll is also the only way to discover a new
    shipment.
    """
    for parcel in active_parcels:
        if parcel["status"] != ParcelStatus.OUT_FOR_DELIVERY:
            continue
        planned_from = parcel.get("planned_from")
        if not planned_from:
            return HOT_INTERVAL_MINUTES
        planned_dt = dt_util.parse_datetime(planned_from)
        if planned_dt is None:
            return HOT_INTERVAL_MINUTES
        if dt_util.as_utc(now) >= dt_util.as_utc(planned_dt) - timedelta(
            hours=HOT_LOOKAHEAD_HOURS
        ):
            return HOT_INTERVAL_MINUTES

    return MID_INTERVAL_MINUTES


def _next_update_interval(now: datetime, tier_minutes: int, entry_id: str) -> timedelta:
    """Turn a tier into the coordinator's next ``update_interval``."""
    if _in_quiet_window(now):
        return _next_anchor(now) - now

    stagger = timedelta(minutes=_stagger_minutes(entry_id))
    candidate = now + timedelta(minutes=tier_minutes) + stagger
    if _in_quiet_window(candidate):
        return _next_anchor(now) - now
    return candidate - now


class PostenBringCoordinator(DataUpdateCoordinator[list[dict]]):
    """Polls the account's parcel list and publishes incoming/outgoing lists.

    ``coordinator.data`` is active incoming parcels, ``self.delivered`` the
    delivered incoming ones. ``self.outgoing``/``self.delivered_outgoing``
    are the ``direction: "send"`` counterparts.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: PostenBringApiClient,
        entry: ConfigEntry,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=HOT_INTERVAL_MINUTES),
        )
        self._client = client
        self.delivered: list[dict] = []
        self.outgoing: list[dict] = []
        self.delivered_outgoing: list[dict] = []
        self._consecutive_429 = 0
        self._consecutive_unauthorized = 0
        self._current_tier_minutes: int | None = None
        self._known_state: dict[str, ParcelStatus] | None = None
        self._known_delivery_times: (
            dict[str, tuple[str | None, str | None]] | None
        ) = None
        self._known_outgoing_state: dict[str, ParcelStatus] | None = None
        self._cached_device_id: str | None = None
        self.last_success_time: datetime | None = None

    @property
    def current_tier_minutes(self) -> int | None:
        """Tier minutes computed on the last refresh (diagnostics only)."""
        return self._current_tier_minutes

    def _device_id(self) -> str | None:
        """Resolve (and cache) this entry's device id for event payloads."""
        if self._cached_device_id is not None:
            return self._cached_device_id
        registry = dr.async_get(self.hass)
        device = next(
            iter(
                dr.async_entries_for_config_entry(registry, self.config_entry.entry_id)
            ),
            None,
        )
        if device is not None:
            self._cached_device_id = device.id
        return self._cached_device_id

    @property
    def _include_history(self) -> bool:
        """Whether the opt-in per-parcel history option is enabled."""
        return bool(
            self.config_entry.options.get(
                CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY
            )
        )

    async def _async_update_data(self) -> list[dict]:
        """Fetch the account's parcels and split into incoming/outgoing × active/delivered."""
        try:
            raws = await self._client.async_get_parcels()
        except PostenBringAuthError as err:
            raise ConfigEntryAuthFailed("Posten Bring session expired") from err
        except PostenBringApiError as err:
            if err.status_code in (401, 403):
                # api.py only gets here with a token the identity provider
                # just accepted, so the credential is fine and one of these
                # is a server-side refusal, not a logout. Only a run of them
                # is worth the user's browser-paste reauth.
                self._consecutive_unauthorized += 1
                if self._consecutive_unauthorized >= MAX_UNAUTHORIZED_POLLS:
                    raise ConfigEntryAuthFailed(
                        "Posten Bring kept refusing a freshly refreshed token"
                    ) from err
                raise UpdateFailed(
                    f"Posten Bring refused a freshly refreshed token "
                    f"(HTTP {err.status_code})"
                ) from err
            if err.status_code != 429:
                raise
            self._consecutive_429 += 1
            retry_after = err.retry_after or min(
                BACKOFF_BASE_SECONDS * 2**self._consecutive_429, BACKOFF_CAP_SECONDS
            )
            raise UpdateFailed(
                "Posten Bring rate-limited (429)", retry_after=retry_after
            ) from err
        self._consecutive_429 = 0
        self._consecutive_unauthorized = 0

        include_history = self._include_history
        incoming_raw = [r for r in raws if parcel_direction(r) != DIRECTION_OUTGOING]
        outgoing_raw = [r for r in raws if parcel_direction(r) == DIRECTION_OUTGOING]

        normalized = [
            normalize_parcel(raw, include_history=include_history) for raw in incoming_raw
        ]
        active = [parcel for parcel in normalized if not parcel["delivered"]]
        delivered = [parcel for parcel in normalized if parcel["delivered"]]

        self.delivered = apply_delivered_filter(
            sort_parcels_by_ts(delivered, "delivered_at", descending=True),
            self.config_entry,
        )
        normalized_active = sort_parcels_by_ts(active, "planned_from")
        incoming = normalized_active + self.delivered
        self._fire_change_events(incoming)
        self._known_state = {
            parcel["barcode"]: parcel["status"]
            for parcel in incoming
            if parcel.get("barcode")
        }
        self._known_delivery_times = {
            parcel["barcode"]: (parcel.get("planned_from"), parcel.get("planned_to"))
            for parcel in incoming
            if parcel.get("barcode")
        }

        normalized_outgoing = [
            normalize_parcel(raw, include_history=include_history) for raw in outgoing_raw
        ]
        outgoing_active = [p for p in normalized_outgoing if not p["delivered"]]
        outgoing_delivered = [p for p in normalized_outgoing if p["delivered"]]
        self.delivered_outgoing = apply_delivered_filter(
            sort_parcels_by_ts(outgoing_delivered, "delivered_at", descending=True),
            self.config_entry,
        )
        self.outgoing = sort_parcels_by_ts(outgoing_active, "planned_from")
        outgoing_all = self.outgoing + self.delivered_outgoing
        self._fire_outgoing_change_events(outgoing_all)
        self._known_outgoing_state = {
            parcel["barcode"]: parcel["status"]
            for parcel in outgoing_all
            if parcel.get("barcode")
        }

        self.last_success_time = datetime.now(timezone.utc)

        now = dt_util.now()
        self._current_tier_minutes = _hottest_tier_minutes(
            normalized_active + outgoing_active, now
        )
        self.update_interval = _next_update_interval(
            now, self._current_tier_minutes, self.config_entry.entry_id
        )
        return normalized_active

    def _fire_change_events(self, parcels: list[dict]) -> None:
        """Fire registered / status-changed / delivered / delivery-time events.

        Silent on the very first refresh — we cannot know which parcels are
        genuinely new versus already present before HA started.
        """
        if self._known_state is None:
            return

        known_times = self._known_delivery_times or {}
        device_id = self._device_id()

        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode:
                continue
            new_status = parcel["status"]
            if barcode not in self._known_state:
                if new_status != ParcelStatus.DELIVERED:
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_parcel_registered",
                        {**parcel, "device_id": device_id},
                    )
                continue

            if self._known_state[barcode] != new_status:
                if new_status == ParcelStatus.DELIVERED:
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_parcel_delivered",
                        {**parcel, "device_id": device_id},
                    )
                else:
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_parcel_status_changed",
                        {
                            **parcel,
                            "device_id": device_id,
                            "old_status": self._known_state[barcode],
                            "new_status": new_status,
                        },
                    )

            old_from, old_to = known_times.get(barcode, (None, None))
            new_from = parcel.get("planned_from")
            new_to = parcel.get("planned_to")
            from_changed = new_from is not None and new_from != old_from
            to_changed = new_to is not None and new_to != old_to
            if from_changed or to_changed:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_parcel_delivery_time_changed",
                    {
                        **parcel,
                        "device_id": device_id,
                        "old_planned_from": old_from,
                        "new_planned_from": new_from,
                        "old_planned_to": old_to,
                        "new_planned_to": new_to,
                    },
                )

    def _fire_outgoing_change_events(self, parcels: list[dict]) -> None:
        """Fire outgoing status-changed / delivered events.

        Mirrors ``_fire_change_events`` but deliberately narrower: no
        ``registered``/``delivery_time_changed`` for outgoing parcels,
        matching the rest of the suite's account-based outgoing model.
        """
        if self._known_outgoing_state is None:
            return

        device_id = self._device_id()
        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode or barcode not in self._known_outgoing_state:
                continue
            new_status = parcel["status"]
            old_status = self._known_outgoing_state[barcode]
            if old_status == new_status:
                continue
            if new_status == ParcelStatus.DELIVERED:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_outgoing_parcel_delivered",
                    {**parcel, "device_id": device_id},
                )
            else:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_outgoing_parcel_status_changed",
                    {
                        **parcel,
                        "device_id": device_id,
                        "old_status": old_status,
                        "new_status": new_status,
                    },
                )
