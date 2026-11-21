"""The parcel contract's checks, run over every normalised sample."""

from datetime import datetime

import pytest

from custom_components.posten_bring.const import ParcelStatus
from custom_components.posten_bring.parcels import (
    _ZONE,
    normalize_parcel,
    parse_iso,
    planned_window,
)

from .payloads import (
    active_parcel,
    archived_delivered_parcel,
    archived_returning_parcel,
    archived_unknown_parcel,
    pickup_parcel,
    returning_parcel,
)

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


def _moment_only() -> dict:
    sample = active_parcel()
    sample["delivery"]["deliveryTime"] = {
        "deliveryWindow": {"start": "2026-04-29T13:00:00Z", "end": None}
    }
    return sample


def _date_only() -> dict:
    sample = active_parcel()
    sample["delivery"]["deliveryTime"] = {"date": "2026-04-28T22:00:00Z"}
    return sample


def _rerouted() -> dict:
    sample = active_parcel()
    sample["pickupPoint"] = {"name": "Example Point Central Station"}
    return sample


SAMPLES = {
    "delivered": archived_delivered_parcel,
    "active": active_parcel,
    "at_pickup_point": pickup_parcel,
    "collected": archived_delivered_parcel,
    "rerouted": _rerouted,
    "returning": returning_parcel,
    "archived_returning": archived_returning_parcel,
    "unknown": archived_unknown_parcel,
    "moment_only": _moment_only,
    "date_only": _date_only,
    "bare": lambda: {"parcelNumber": "PB000000001NO"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_planned_is_filled_while_the_parcel_is_active():
    parcel = normalize_parcel(active_parcel())
    assert parcel["planned_from"] == "2026-04-29T13:00:00Z"
    assert parcel["planned_to"] == "2026-04-29T15:00:00Z"


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(archived_delivered_parcel())
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] is None


def test_a_pickup_product_is_pickup_without_events():
    sample = active_parcel()
    sample["productName"] = "pickup_parcel_bulk"
    assert normalize_parcel(sample)["pickup"] is True


def test_a_rerouted_parcel_is_pickup_before_it_arrives():
    parcel = normalize_parcel(_rerouted())
    assert parcel["status"] is ParcelStatus.IN_TRANSIT
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Example Point Central Station"


def test_a_pickup_parcel_without_a_named_point_has_no_pickup_point():
    raw = pickup_parcel()
    raw["pickupPoint"] = None
    parcel = normalize_parcel(raw)
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] is None


def test_a_moment_ends_at_the_end_of_that_day():
    parcel = normalize_parcel(_moment_only())
    end = datetime.fromisoformat(parcel["planned_to"])
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert end.date() == parse_iso(parcel["planned_from"]).astimezone(end.tzinfo).date()


def test_a_date_covers_the_whole_day():
    parcel = normalize_parcel(_date_only())
    start = datetime.fromisoformat(parcel["planned_from"])
    end = datetime.fromisoformat(parcel["planned_to"])
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert start.date() == end.date()
    assert start.isoformat() == "2026-04-29T00:00:00+02:00"


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (None, None, (None, None)),
        ("garbage", None, (None, None)),
        (
            "2026-04-29T00:00:00",
            None,
            ("2026-04-29T00:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (
            "2026-04-29T15:00:00Z",
            "2026-04-29T13:00:00Z",
            ("2026-04-29T13:00:00Z", "2026-04-29T15:00:00Z"),
        ),
        (
            "2026-04-29T13:00:00Z",
            "2026-04-29T13:00:00Z",
            ("2026-04-29T13:00:00Z", "2026-04-29T23:59:59+02:00"),
        ),
        (
            "2026-04-29",
            "2026-05-01",
            ("2026-04-29T00:00:00+02:00", "2026-05-01T23:59:59+02:00"),
        ),
        ("2026-04-29T23:59:59+02:00", None, ("2026-04-29T23:59:59+02:00", None)),
    ],
)
def test_planned_window_shapes(start, end, expected):
    assert planned_window(start, end, zone=_ZONE) == expected
