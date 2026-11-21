"""Tests for Posten Bring sensor property logic."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from custom_components.posten_bring.const import ParcelStatus
from custom_components.posten_bring.sensor import (
    PostenBringAwaitingPickupSensor,
    PostenBringDeliveredParcelsSensor,
    PostenBringEnRouteToPickupPointSensor,
    PostenBringIncomingParcelsSensor,
    PostenBringLastUpdateSensor,
    PostenBringNextDeliverySensor,
    PostenBringOutgoingDeliveredParcelsSensor,
    PostenBringOutgoingParcelsSensor,
    PostenBringParcelSensor,
)


def _entry(entry_id: str = "e1") -> MagicMock:
    entry = MagicMock()
    entry.entry_id = entry_id
    return entry


def _coordinator(
    data: list[dict],
    delivered: list[dict] | None = None,
    outgoing: list[dict] | None = None,
    delivered_outgoing: list[dict] | None = None,
) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.delivered = delivered if delivered is not None else []
    coordinator.outgoing = outgoing if outgoing is not None else []
    coordinator.delivered_outgoing = (
        delivered_outgoing if delivered_outgoing is not None else []
    )
    return coordinator


def _parcel(
    barcode: str,
    status: ParcelStatus = ParcelStatus.IN_TRANSIT,
    pickup: bool = False,
    planned_from: str | None = None,
    planned_to: str | None = None,
) -> dict:
    return {
        "carrier": "Posten Bring",
        "barcode": barcode,
        "sender": "Sender",
        "receiver": None,
        "status": status,
        "pickup": pickup,
        "planned_from": planned_from,
        "planned_to": planned_to,
    }


def test_incoming_counts_and_lists():
    coordinator = _coordinator([_parcel("A"), _parcel("B")])
    sensor = PostenBringIncomingParcelsSensor(
        coordinator, _entry(), lambda _: None, set()
    )
    assert sensor.native_value == 2
    assert len(sensor.extra_state_attributes["parcels"]) == 2


def test_parcel_sensor_status_and_attributes():
    parcel = _parcel("A", status=ParcelStatus.OUT_FOR_DELIVERY)
    sensor = PostenBringParcelSensor(_coordinator([parcel]), _entry(), "A")
    assert sensor.native_value == ParcelStatus.OUT_FOR_DELIVERY
    assert sensor.extra_state_attributes["barcode"] == "A"


def test_parcel_sensor_missing_barcode():
    sensor = PostenBringParcelSensor(_coordinator([_parcel("A")]), _entry(), "OTHER")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_next_delivery_picks_earliest():
    coordinator = _coordinator(
        [
            _parcel("A", planned_from="2099-05-02T10:00:00Z"),
            _parcel("B", planned_from="2099-05-01T10:00:00Z"),
        ]
    )
    sensor = PostenBringNextDeliverySensor(coordinator, _entry())
    assert sensor.native_value == datetime(2099, 5, 1, 10, 0, tzinfo=UTC)
    assert sensor.extra_state_attributes["barcode"] == "B"


def test_next_delivery_none_without_moments():
    sensor = PostenBringNextDeliverySensor(_coordinator([_parcel("A")]), _entry())
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_next_delivery_skips_unparseable_moment():
    coordinator = _coordinator(
        [
            _parcel("A", planned_from="not-a-date"),
            _parcel("B", planned_from="2099-05-01T10:00:00Z"),
        ]
    )
    sensor = PostenBringNextDeliverySensor(coordinator, _entry())
    assert sensor.extra_state_attributes["barcode"] == "B"


def test_delivered_sensor():
    coordinator = _coordinator(
        [], delivered=[_parcel("D", status=ParcelStatus.DELIVERED)]
    )
    sensor = PostenBringDeliveredParcelsSensor(coordinator, _entry())
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["parcels"][0]["barcode"] == "D"


def test_last_update_sensor():
    coordinator = _coordinator([])
    moment = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
    coordinator.last_success_time = moment
    sensor = PostenBringLastUpdateSensor(coordinator, _entry())
    assert sensor.native_value == moment


def test_outgoing_parcels_sensor():
    coordinator = _coordinator([], outgoing=[_parcel("O")])
    sensor = PostenBringOutgoingParcelsSensor(coordinator, _entry())
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["parcels"][0]["barcode"] == "O"


def test_outgoing_delivered_parcels_sensor():
    coordinator = _coordinator(
        [], delivered_outgoing=[_parcel("D", status=ParcelStatus.DELIVERED)]
    )
    sensor = PostenBringOutgoingDeliveredParcelsSensor(coordinator, _entry())
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["parcels"][0]["barcode"] == "D"


def test_awaiting_pickup_sensor_counts_only_pickup_at_point():
    coordinator = _coordinator(
        [
            _parcel("A", status=ParcelStatus.AT_PICKUP_POINT, pickup=True),
            _parcel("B", status=ParcelStatus.IN_TRANSIT),
        ]
    )
    sensor = PostenBringAwaitingPickupSensor(coordinator, _entry())
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["parcels"][0]["barcode"] == "A"


def test_awaiting_pickup_counts_rerouted_parcel_without_pickup_flag():
    rerouted = _parcel("REROUTED", status=ParcelStatus.AT_PICKUP_POINT)
    sensor = PostenBringAwaitingPickupSensor(
        _coordinator([rerouted, _parcel("HOME")]), _entry()
    )
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["parcels"] == [rerouted]


def test_next_delivery_skips_a_window_that_has_ended():
    coordinator = _coordinator(
        [
            _parcel("OLD", planned_from="2000-01-01T10:00:00Z"),
            _parcel(
                "OLD-WINDOW",
                planned_from="2000-01-01T10:00:00Z",
                planned_to="2000-01-01T12:00:00Z",
            ),
            _parcel("NEXT", planned_from="2099-01-01T10:00:00Z"),
        ]
    )
    sensor = PostenBringNextDeliverySensor(coordinator, _entry())
    assert sensor.extra_state_attributes["barcode"] == "NEXT"


def test_en_route_sensor_counts_pickup_parcels_not_yet_there():
    en_route = _parcel("E", status=ParcelStatus.IN_TRANSIT)
    en_route["pickup"] = True
    arrived = _parcel("A", status=ParcelStatus.AT_PICKUP_POINT)
    arrived["pickup"] = True
    returning = _parcel("R", status=ParcelStatus.RETURNING)
    returning["pickup"] = True
    home = _parcel("H", status=ParcelStatus.IN_TRANSIT)
    home["pickup"] = False
    coordinator = _coordinator([en_route, arrived, returning, home])
    sensor = PostenBringEnRouteToPickupPointSensor(coordinator, _entry())
    assert sensor.native_value == 1
    assert [p["barcode"] for p in sensor.extra_state_attributes["parcels"]] == ["E"]
    awaiting = PostenBringAwaitingPickupSensor(coordinator, _entry())
    assert not {p["barcode"] for p in sensor.extra_state_attributes["parcels"]} & {
        p["barcode"] for p in awaiting.extra_state_attributes["parcels"]
    }
