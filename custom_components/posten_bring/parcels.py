"""Canonical parcel shape, status mapping and list helpers.

Everything here is a **pure function** — no I/O, no Home Assistant objects
beyond the config entry's options.

Status/product/event values are lowercase ``snake_case`` on the wire, not the
``SCREAMING_SNAKE_CASE`` the APK's enum member names implied (a live capture
confirmed ``status: "archived"``, not ``"ARCHIVED"`` — see
carrier-research/posten-bring/posten-bring.md's ``### Status vocabulary``).
Every comparison below lowercases both the raw value and the map's keys
first — never hardcode an uppercase spelling.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    NEW_ISSUE_URL,
    PUBLIC_TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Item-level ParcelStatus -> canonical, per posten-bring.md's `### Status
# vocabulary`. Keys are already lowercase; map_parcel_status() lowercases the
# raw value before lookup, never the other way round.
_STATUS_MAP: dict[str, ParcelStatus] = {
    "notified": ParcelStatus.REGISTERED,
    "underway": ParcelStatus.IN_TRANSIT,
    "collectable": ParcelStatus.AT_PICKUP_POINT,
    "return_underway": ParcelStatus.RETURNING,
    "return_collectable": ParcelStatus.RETURNING,
    "unknown": ParcelStatus.UNKNOWN,
}

# ARCHIVED[_BY_USER] carries no terminal-state information by itself — the
# item-level enum has no DELIVERED member. Derive it from the most recent
# event in events[] instead (posten-bring.md: "There is no item-level
# DELIVERED ... map delivery from event history, not merely current item
# status").
_ARCHIVED_STATUSES = {"archived", "archived_by_user"}
_DELIVERED_EVENT_TYPES = {"delivered", "delivered_bag_on_door"}
_RETURNING_EVENT_TYPES = {"return", "delivered_sender"}

# Directions seen on the wire (live-confirmed: "receive"). "send" is the
# documented outgoing counterpart; anything else falls back to incoming with
# a one-shot warning rather than being silently dropped.
DIRECTION_INCOMING = "receive"
DIRECTION_OUTGOING = "send"

_unmapped_statuses_logged: set[str] = set()
_unmapped_directions_logged: set[str] = set()
_undetermined_archived_logged: set[str] = set()


def _warn_unmapped_status(raw_status: str) -> None:
    if raw_status in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(raw_status)
    _LOGGER.warning(
        "Unrecognised Posten Bring status — help us map it. Open an issue "
        "and paste this line: %s\n  status=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        raw_status,
    )


def _warn_undetermined_archived(barcode: str | None) -> None:
    # barcode is only a dedup key here, never logged — it identifies a real
    # parcel and this warning must stay safe to paste into a public issue.
    key = barcode or "<unknown>"
    if key in _undetermined_archived_logged:
        return
    _undetermined_archived_logged.add(key)
    _LOGGER.warning(
        "A Posten Bring parcel is archived but no delivered/returning event "
        "was found in its event history — reporting 'unknown'. Open an "
        "issue and mention this line: %s",
        NEW_ISSUE_URL,
    )


def _warn_unmapped_direction(direction: str) -> None:
    if direction in _unmapped_directions_logged:
        return
    _unmapped_directions_logged.add(direction)
    _LOGGER.warning(
        "Unrecognised Posten Bring parcel direction — treating it as "
        "incoming. Open an issue and paste this line: %s\n  direction=%s",
        NEW_ISSUE_URL,
        direction,
    )


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing
    on a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Values are strings on this API; passed through untouched (their
    consumers are guarded by :func:`parse_iso`). A bare number is treated as
    epoch milliseconds, matching the rest of the suite, in case a future
    payload ever stamps one that way.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def _most_recent_event_of_types(events: list[Any], types: set[str]) -> dict | None:
    """Return the latest event whose (lowercased) ``type`` is in ``types``.

    A parcel added to the account *after* it was already delivered gets a
    bookkeeping ``added_by_user`` event stamped with today's date — which
    outranks the real ``delivered`` event already in its history if picked
    by raw recency alone (confirmed against a real account, 2026-09-03: a
    picked-up parcel's newest event was ``added_by_user``, one day after an
    older ``delivered`` event, and the naive "latest event of any type"
    approach reported ``unknown``). Filtering to the relevant type family
    before taking the max avoids that.
    """
    parseable: list[tuple[datetime, dict]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("type") or "").lower() not in types:
            continue
        parsed = parse_iso(to_iso_timestamp(event.get("date")))
        if parsed is not None:
            parseable.append((parsed, event))
    if not parseable:
        return None
    return max(parseable, key=lambda item: item[0])[1]


_CONCLUSIVE_EVENT_TYPES = _DELIVERED_EVENT_TYPES | _RETURNING_EVENT_TYPES


def _derive_status_from_events(
    events: list[Any], *, barcode: str | None
) -> ParcelStatus:
    """Derive a canonical status for an ARCHIVED[_BY_USER] item from its events.

    Picks the most recent event among the delivered/returning families only —
    see :func:`_most_recent_event_of_types` — then classifies by its type, so
    a later conclusive event of the *other* family (delivered, then later
    returned) still wins over an earlier one.
    """
    latest_conclusive = _most_recent_event_of_types(events, _CONCLUSIVE_EVENT_TYPES)
    event_type = str(latest_conclusive.get("type") or "").lower() if latest_conclusive else ""
    if event_type in _DELIVERED_EVENT_TYPES:
        return ParcelStatus.DELIVERED
    if event_type in _RETURNING_EVENT_TYPES:
        return ParcelStatus.RETURNING
    _warn_undetermined_archived(barcode)
    return ParcelStatus.UNKNOWN


def map_parcel_status(
    raw_status: str | None, events: list[Any] | None, *, barcode: str | None = None
) -> ParcelStatus:
    """Map the item-level ``status`` (case-insensitively) to a canonical status."""
    if not raw_status:
        return ParcelStatus.UNKNOWN
    normalized = raw_status.strip().lower()
    if normalized in _ARCHIVED_STATUSES:
        return _derive_status_from_events(events or [], barcode=barcode)
    mapped = _STATUS_MAP.get(normalized)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(raw_status)
    return ParcelStatus.UNKNOWN


def build_history(
    events: list[Any] | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from ``events[]``.

    Each entry is ``{timestamp, status, raw_status}``. ``status`` stays
    ``None`` on every entry — ``EventType`` is a distinct, open vocabulary
    from the item-level ``ParcelStatus`` (see posten-bring.md's `### Status
    vocabulary`) and mapping it onto the same enum per-event would invent a
    correspondence no source confirms; ``raw_status`` carries the event's own
    ``type`` (or its ``description``, when present) so the history stays
    useful without pretending to a canonical status.
    """
    parseable: list[tuple[datetime, dict]] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = to_iso_timestamp(event.get("date"))
        if not timestamp:
            continue
        parsed = parse_iso(timestamp)
        if parsed is None:
            continue
        parseable.append(
            (
                parsed,
                {
                    "timestamp": timestamp,
                    "status": None,
                    "raw_status": event.get("description") or event.get("type"),
                },
            )
        )
    parseable.sort(key=lambda item: item[0])
    return [entry for _, entry in parseable][-max_events:]


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: centimetres — Posten Bring already reports
    ``dimensions.{length,width,height}InCm``, no conversion needed.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{int(length)} x {int(width)} x {int(height)} cm",
    }


def _delivery_window(delivery: dict) -> tuple[str | None, str | None]:
    """Return ``(planned_from, planned_to)``, preferring the window over a bare date.

    ``delivery.deliveryTime.date`` can be local midnight encoded as UTC, so
    it is only used when no window is present at all (posten-bring.md).
    """
    delivery_time = delivery.get("deliveryTime")
    delivery_time = delivery_time if isinstance(delivery_time, dict) else {}
    window = delivery_time.get("deliveryWindow")
    window = window if isinstance(window, dict) else {}
    start = to_iso_timestamp(window.get("start"))
    end = to_iso_timestamp(window.get("end"))
    if start or end:
        return start, end
    return to_iso_timestamp(delivery_time.get("date")), None


def tracking_url(barcode: str | None) -> str | None:
    """Construct the public, keyless tracking deep-link for a parcel."""
    if not barcode:
        return None
    return PUBLIC_TRACKING_URL.format(barcode=barcode)


def parcel_direction(raw: dict) -> str:
    """Return the normalised direction ("receive"/"send") for a raw parcel.

    Unrecognised values default to incoming with a one-shot warning rather
    than being dropped — see ``direction`` in posten-bring.md's live capture.
    """
    direction = str(raw.get("direction") or "").strip().lower()
    if direction in (DIRECTION_INCOMING, DIRECTION_OUTGOING):
        return direction
    if direction:
        _warn_unmapped_direction(direction)
    return DIRECTION_INCOMING


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The **keys of the returned dict are the contract** — see
    canonical-shape.md. Every optional field is set to ``None`` rather than
    omitted when Posten Bring does not report it for a given parcel.
    """
    barcode = raw.get("parcelNumber")
    raw_status = raw.get("status")
    events = raw.get("events") if isinstance(raw.get("events"), list) else []
    status = map_parcel_status(raw_status, events, barcode=barcode)
    delivered = status is ParcelStatus.DELIVERED

    sender = raw.get("sender") or {}
    recipient = raw.get("recipient") or {}

    delivery = raw.get("delivery") or {}
    delivery = delivery if isinstance(delivery, dict) else {}
    planned_from, planned_to = (None, None) if delivered else _delivery_window(delivery)

    pickup_point = raw.get("pickupPoint") or {}
    pickup_point = pickup_point if isinstance(pickup_point, dict) else {}
    is_pickup = status is ParcelStatus.AT_PICKUP_POINT

    dimensions = raw.get("dimensions") or {}
    dimensions = dimensions if isinstance(dimensions, dict) else {}

    # The most recent DELIVERED-family event's own timestamp is the best
    # available delivered_at — the item-level payload carries no dedicated
    # "delivered at" field of its own. Filtered to that family specifically
    # (not "the most recent event overall"): a parcel added to the account
    # after it was already delivered gets a later, non-conclusive
    # added_by_user event that must not be mistaken for the delivery time.
    delivered_at = None
    if delivered:
        latest = _most_recent_event_of_types(events, _DELIVERED_EVENT_TYPES)
        delivered_at = to_iso_timestamp(latest.get("date")) if latest else None

    return {
        "carrier": "Posten Bring",
        "barcode": barcode,
        "sender": sender.get("name") if isinstance(sender, dict) else None,
        "receiver": recipient.get("name") if isinstance(recipient, dict) else None,
        "status": status,
        "raw_status": raw_status,
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": planned_from,
        "planned_to": planned_to,
        "pickup": is_pickup,
        "pickup_point": pickup_point.get("name") if is_pickup else None,
        "url": tracking_url(barcode),
        "weight": raw.get("weightInKg"),
        "dimensions": format_dimensions(
            dimensions.get("lengthInCm"),
            dimensions.get("widthInCm"),
            dimensions.get("heightInCm"),
        ),
        "history": build_history(events) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    Parcels whose value is missing or unparseable always sort to the end,
    regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
