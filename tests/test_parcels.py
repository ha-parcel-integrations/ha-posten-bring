"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping can be tested
as plain functions.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.posten_bring.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    ParcelStatus,
)
from custom_components.posten_bring.parcels import (
    apply_delivered_filter,
    build_history,
    format_dimensions,
    map_parcel_status,
    normalize_parcel,
    parcel_direction,
    parse_iso,
    sort_parcels_by_ts,
    to_iso_timestamp,
)

from .payloads import (
    active_parcel,
    archived_bag_on_door_parcel,
    archived_delivered_parcel,
    archived_returning_parcel,
    archived_unknown_parcel,
    event,
    outgoing_parcel,
    pickup_parcel,
    returning_parcel,
)

# ---------------------------------------------------------------------------
# map_parcel_status — including case-insensitivity, the plan's central trap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        ("notified", ParcelStatus.REGISTERED),
        ("underway", ParcelStatus.IN_TRANSIT),
        ("collectable", ParcelStatus.AT_PICKUP_POINT),
        ("return_underway", ParcelStatus.RETURNING),
        ("return_collectable", ParcelStatus.RETURNING),
        ("unknown", ParcelStatus.UNKNOWN),
    ],
)
def test_map_parcel_status_known(code, expected):
    assert map_parcel_status(code, []) == expected


@pytest.mark.parametrize(
    "code",
    ["NOTIFIED", "Underway", "COLLECTABLE", "Return_Underway", "RETURN_COLLECTABLE"],
)
def test_map_parcel_status_is_case_insensitive(code):
    """The wire format is lowercase; the APK enum's SCREAMING_SNAKE_CASE must
    never be hardcoded or relied on — this is the band-ordering trap the
    build plan names explicitly."""
    expected = {
        "NOTIFIED": ParcelStatus.REGISTERED,
        "UNDERWAY": ParcelStatus.IN_TRANSIT,
        "COLLECTABLE": ParcelStatus.AT_PICKUP_POINT,
        "RETURN_UNDERWAY": ParcelStatus.RETURNING,
        "RETURN_COLLECTABLE": ParcelStatus.RETURNING,
    }[code.upper()]
    assert map_parcel_status(code, []) == expected


def test_map_parcel_status_missing_is_unknown():
    assert map_parcel_status(None, []) == ParcelStatus.UNKNOWN
    assert map_parcel_status("", []) == ParcelStatus.UNKNOWN


def test_map_parcel_status_unmapped_is_unknown():
    assert map_parcel_status("teleported", []) == ParcelStatus.UNKNOWN


def test_unmapped_status_warns_only_once(caplog):
    assert map_parcel_status("abducted", []) == ParcelStatus.UNKNOWN
    assert map_parcel_status("abducted", []) == ParcelStatus.UNKNOWN
    assert caplog.text.count("abducted") == 1
    assert "issues/new" in caplog.text


# ---------------------------------------------------------------------------
# ARCHIVED[_BY_USER] — no item-level DELIVERED; derive from events[]
# ---------------------------------------------------------------------------


def test_archived_with_delivered_event_maps_to_delivered():
    events = [event("added_by_user", "2026-04-27T00:00:00Z"), event("delivered", "2026-04-29T13:12:42Z")]
    assert map_parcel_status("archived", events) == ParcelStatus.DELIVERED


def test_archived_with_delivered_bag_on_door_event_maps_to_delivered():
    events = [event("delivered_bag_on_door", "2026-04-29T13:12:42Z")]
    assert map_parcel_status("archived_by_user", events) == ParcelStatus.DELIVERED


@pytest.mark.parametrize("event_type", ["return", "delivered_sender"])
def test_archived_with_returning_event_maps_to_returning(event_type):
    events = [event(event_type, "2026-04-29T13:12:42Z")]
    assert map_parcel_status("archived", events) == ParcelStatus.RETURNING


def test_archived_picks_the_most_recent_event_not_list_order():
    events = [
        event("delivered", "2026-04-29T13:12:42Z"),
        event("return", "2026-05-01T09:00:00Z"),  # later, out of list order
    ]
    assert map_parcel_status("archived", events) == ParcelStatus.RETURNING


def test_archived_added_to_account_after_delivery_still_maps_to_delivered():
    """Confirmed against a real account (see posten-bring.md's Log,
    2026-09-03): a parcel added to the account after pickup gets a
    bookkeeping ``added_by_user`` event stamped with today's date — newer
    than the real ``delivered`` event already in its history. Picking "most
    recent event of any type" would report ``unknown``; only the
    delivered/returning family should be compared.
    """
    events = [
        event("pre_notified", "2026-04-25T10:00:00Z"),
        event("ready_for_pick_up", "2026-04-26T11:00:00Z"),
        event("delivered", "2026-04-28T15:30:00Z"),
        event("added_by_user", "2026-04-30T09:00:00Z"),
    ]
    assert map_parcel_status("archived", events) == ParcelStatus.DELIVERED


def test_archived_with_no_conclusive_event_is_unknown_and_warns_once(caplog):
    events = [event("added_by_user", "2026-04-27T00:00:00Z")]
    assert map_parcel_status("archived", events, barcode="PB1") == ParcelStatus.UNKNOWN
    assert map_parcel_status("archived", events, barcode="PB1") == ParcelStatus.UNKNOWN
    assert caplog.text.count("archived but no delivered/returning event") == 1
    # The barcode identifies a real parcel and must never reach a log line
    # that's meant to be safe to paste into a public issue.
    assert "PB1" not in caplog.text


def test_archived_with_no_events_at_all_is_unknown():
    assert map_parcel_status("archived", []) == ParcelStatus.UNKNOWN


# ---------------------------------------------------------------------------
# direction
# ---------------------------------------------------------------------------


def test_parcel_direction_incoming_and_outgoing():
    assert parcel_direction({"direction": "receive"}) == "receive"
    assert parcel_direction({"direction": "send"}) == "send"


def test_parcel_direction_is_case_insensitive():
    assert parcel_direction({"direction": "RECEIVE"}) == "receive"


def test_parcel_direction_unknown_defaults_to_incoming_and_warns_once(caplog):
    assert parcel_direction({"direction": "sideways"}) == "receive"
    assert parcel_direction({"direction": "sideways"}) == "receive"
    assert caplog.text.count("sideways") == 1


def test_parcel_direction_missing_defaults_to_incoming_silently(caplog):
    assert parcel_direction({}) == "receive"
    assert "issues/new" not in caplog.text


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    assert parse_iso("2026-04-29T13:12:42").tzinfo == timezone.utc
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_passes_strings_through_and_converts_epoch_ms():
    assert to_iso_timestamp("2026-04-29T13:12:42Z") == "2026-04-29T13:12:42Z"
    assert to_iso_timestamp(1784203767167) == "2026-07-16T12:09:27.167000+00:00"
    assert to_iso_timestamp(None) is None
    assert to_iso_timestamp(10**20) is None  # out of range -> None, never raises


def test_format_dimensions_needs_all_three_axes():
    assert format_dimensions(30, 20, 10) == {
        "length": 30,
        "width": 20,
        "height": 10,
        "text": "30 x 20 x 10 cm",
    }
    assert format_dimensions(30, None, 10) is None


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def test_build_history_orders_oldest_to_newest():
    history = build_history(archived_delivered_parcel()["events"])
    assert history[-1]["raw_status"] == "delivered"


def test_build_history_caps_to_max_events():
    events = [event("underway", f"2026-04-{day:02d}T10:00:00Z") for day in range(1, 26)]
    assert len(build_history(events, max_events=20)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"type": "underway"}]) == []  # no date
    assert build_history(["not-a-dict"]) == []


def test_build_history_status_stays_null():
    """EventType is a distinct, open vocabulary from ParcelStatus — history
    entries never invent a canonical status mapping for it."""
    history = build_history([event("pre_notified", "2026-04-24T10:00:00Z")])
    assert history[0]["status"] is None


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def test_normalize_publishes_exactly_the_canonical_keys():
    assert list(normalize_parcel(archived_delivered_parcel())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    assert CAPABILITIES <= KNOWN_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    delivered = normalize_parcel(archived_delivered_parcel())
    active = normalize_parcel(active_parcel())
    pickup = normalize_parcel(pickup_parcel())
    with_history = normalize_parcel(archived_delivered_parcel(), include_history=True)

    if "weight" in CAPABILITIES:
        assert delivered["weight"] is not None
    if "dimensions" in CAPABILITIES:
        assert delivered["dimensions"] is not None
    if "delivery_window" in CAPABILITIES:
        assert active["planned_from"] is not None or active["planned_to"] is not None
    if "pickup_point" in CAPABILITIES:
        assert pickup["pickup_point"] is not None
    if "url" in CAPABILITIES:
        assert delivered["url"] is not None
    if "history" in CAPABILITIES:
        assert with_history["history"] is not None


def test_normalize_delivered_at_ignores_a_later_added_by_user_event():
    """delivered_at must be the delivered event's own timestamp, not whatever
    event happens to be temporally last — see the status-derivation test of
    the same real-world scenario above."""
    raw = archived_delivered_parcel()
    raw["events"].append(event("added_by_user", "2026-05-01T00:00:00Z"))
    parcel = normalize_parcel(raw)
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["delivered_at"] == "2026-04-29T13:12:42Z"


def test_normalize_delivered_parcel():
    raw = archived_delivered_parcel()
    parcel = normalize_parcel(raw)
    assert parcel["carrier"] == "Posten Bring"
    assert parcel["barcode"] == raw["parcelNumber"]
    assert parcel["sender"] == "Example Shop"
    assert parcel["receiver"] == "Jane Doe"
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "archived"
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-04-29T13:12:42Z"
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["url"] == f"https://sporing.posten.no/sporing/{raw['parcelNumber']}"
    assert parcel["weight"] == 1.25
    assert parcel["dimensions"]["text"] == "30 x 20 x 10 cm"
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_archived_returning_parcel():
    parcel = normalize_parcel(archived_returning_parcel())
    assert parcel["status"] == ParcelStatus.RETURNING
    assert parcel["delivered"] is False


def test_normalize_archived_bag_on_door_parcel():
    parcel = normalize_parcel(archived_bag_on_door_parcel())
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["delivered"] is True


def test_normalize_archived_unknown_parcel():
    parcel = normalize_parcel(archived_unknown_parcel())
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(archived_delivered_parcel(), include_history=True)
    assert parcel["history"] is not None
    assert parcel["history"][-1]["raw_status"] == "delivered"


def test_normalize_active_parcel_has_window():
    parcel = normalize_parcel(active_parcel())
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert parcel["delivered"] is False
    assert parcel["planned_from"] == "2026-04-29T13:00:00Z"
    assert parcel["planned_to"] == "2026-04-29T15:00:00Z"


def test_normalize_falls_back_to_date_only_when_no_window():
    raw = active_parcel()
    raw["delivery"]["deliveryTime"] = {"date": "2026-04-29T00:00:00Z"}
    parcel = normalize_parcel(raw)
    assert parcel["planned_from"] == "2026-04-29T00:00:00Z"
    assert parcel["planned_to"] is None


def test_normalize_prefers_window_over_bare_date_when_both_present():
    raw = active_parcel()
    raw["delivery"]["deliveryTime"]["date"] = "2026-04-28T00:00:00Z"
    parcel = normalize_parcel(raw)
    assert parcel["planned_from"] == "2026-04-29T13:00:00Z"


def test_normalize_pickup_parcel():
    parcel = normalize_parcel(pickup_parcel())
    assert parcel["status"] == ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Example Point Central Station"


def test_normalize_returning_parcel():
    parcel = normalize_parcel(returning_parcel())
    assert parcel["status"] == ParcelStatus.RETURNING
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_normalize_missing_nested_objects_stay_none():
    """Every nested delivery/recipient/pickup-point/window object is optional."""
    parcel = normalize_parcel({"parcelNumber": "PB1", "status": "underway"})
    assert parcel["sender"] is None
    assert parcel["receiver"] is None
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["pickup_point"] is None
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None


def test_normalize_keeps_raw_payload():
    raw = active_parcel()
    assert normalize_parcel(raw)["raw"] is raw


def test_normalize_preserves_product_name_without_validating_it():
    """ProductName is confirmed not a closed enum — never validate/reject on it."""
    raw = active_parcel()
    raw["productName"] = "pickup_parcel_bulk"
    parcel = normalize_parcel(raw)
    assert parcel["raw"]["productName"] == "pickup_parcel_bulk"


def test_normalize_incoming_and_outgoing_direction():
    incoming = normalize_parcel(active_parcel())
    outgoing = normalize_parcel(outgoing_parcel())
    assert incoming["raw"]["direction"] == "receive"
    assert outgoing["raw"]["direction"] == "send"
    # normalize_parcel itself doesn't drop direction-specific fields — the
    # coordinator is what splits by direction.
    assert outgoing["status"] == incoming["status"]


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels
