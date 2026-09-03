"""Synthetic Posten Bring inbox payloads shared by the test modules.

No redacted real capture is pre-approved for this repo (see
carrier-research/posten-bring/api/README.md) — these are synthetic fixtures
built to the documented payload shape (field names, lowercase snake_case
wire values, nested paths) rather than invented "approved" ones. Keep them
in one module rather than inline in each test — when the payload shape turns
out to differ from what's assumed here, there is exactly one place to fix.
"""
from __future__ import annotations

ACTIVE_NUMBER = "PB999999999NO"
DELIVERED_NUMBER = "PB123456789NO"
PICKUP_NUMBER = "PB555555555SE"
ARCHIVED_DELIVERED_NUMBER = "PB444444444SE"
ARCHIVED_RETURNING_NUMBER = "PB333333333DK"
ARCHIVED_UNKNOWN_NUMBER = "PB222222222NO"
OUTGOING_NUMBER = "PB777777777NO"


def event(event_type: str, date: str, description: str | None = None) -> dict:
    """One entry of the carrier's own event timeline (lowercase wire values)."""
    return {
        "type": event_type,
        "date": date,
        "description": description or event_type,
        "cause": None,
        "countryCode": "NO",
        "displayStatus": "significant",
    }


def active_parcel(number: str = ACTIVE_NUMBER) -> dict:
    """An "underway" incoming parcel with a delivery window."""
    return {
        "parcelNumber": number,
        "consignmentNumber": f"C{number}",
        "status": "underway",
        "direction": "receive",
        "sender": {"name": "Example Shop"},
        "recipient": {"name": "Jane Doe"},
        "productName": "norway_parcel",
        "productGroup": "mailbox",
        "weightInKg": 1.25,
        "dimensions": {"lengthInCm": 30, "widthInCm": 20, "heightInCm": 10},
        "delivery": {
            "deliveryTime": {
                "date": "2026-04-29T00:00:00Z",
                "deliveryWindow": {
                    "start": "2026-04-29T13:00:00Z",
                    "end": "2026-04-29T15:00:00Z",
                },
            }
        },
        "pickupPoint": None,
        "events": [
            event("pre_notified", "2026-04-27T23:03:58Z"),
            event("added_by_user", "2026-04-28T08:00:00Z"),
        ],
    }


def pickup_parcel(number: str = PICKUP_NUMBER) -> dict:
    """A "collectable" parcel waiting at a pickup point."""
    parcel = active_parcel(number)
    parcel.update(
        {
            "status": "collectable",
            "pickupPoint": {"name": "Example Point Central Station"},
        }
    )
    return parcel


def returning_parcel(number: str = "PB666666666NO") -> dict:
    """A "return_underway" parcel."""
    parcel = active_parcel(number)
    parcel["status"] = "return_underway"
    return parcel


def archived_delivered_parcel(number: str = ARCHIVED_DELIVERED_NUMBER) -> dict:
    """An archived parcel whose most recent event is a delivery."""
    parcel = active_parcel(number)
    parcel.update(
        {
            "status": "archived",
            "events": [
                *parcel["events"],
                event("ready_for_pick_up", "2026-04-29T08:00:00Z"),
                event("delivered", "2026-04-29T13:12:42Z"),
            ],
        }
    )
    return parcel


def archived_bag_on_door_parcel(number: str = "PB111111111NO") -> dict:
    """An archived parcel delivered via the bag-on-door event variant."""
    parcel = active_parcel(number)
    parcel.update(
        {
            "status": "archived_by_user",
            "events": [
                *parcel["events"],
                event("delivered_bag_on_door", "2026-04-29T13:12:42Z"),
            ],
        }
    )
    return parcel


def archived_returning_parcel(number: str = ARCHIVED_RETURNING_NUMBER) -> dict:
    """An archived parcel whose most recent event is a return."""
    parcel = active_parcel(number)
    parcel.update(
        {
            "status": "archived",
            "events": [
                *parcel["events"],
                event("return", "2026-04-30T09:00:00Z"),
            ],
        }
    )
    return parcel


def archived_unknown_parcel(number: str = ARCHIVED_UNKNOWN_NUMBER) -> dict:
    """An archived parcel with no delivered/returning event — stays unknown."""
    parcel = active_parcel(number)
    parcel["status"] = "archived"
    return parcel


def outgoing_parcel(number: str = OUTGOING_NUMBER) -> dict:
    """An outgoing ("send") parcel, still underway."""
    parcel = active_parcel(number)
    parcel["direction"] = "send"
    return parcel


def inbox_page(
    parcels: list[dict], *, remaining_count: int = 0, total_count: int | None = None
) -> dict:
    """One page of the inbox envelope, per posten-bring.md's live capture."""
    return {
        "parcels": parcels,
        "remainingCount": remaining_count,
        "totalCount": total_count if total_count is not None else len(parcels),
        "providedLastUpdated": "2026-04-01T00:00:00Z",
        "deleteParcelNumbers": [],
        "failedParcelNumbers": [],
    }
