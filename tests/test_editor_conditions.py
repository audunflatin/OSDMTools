"""Salgsperiode, reisegyldighet og antall passasjerer per billett i OSDM-editoren."""
import json

import pytest

from backend import osdm_editor as oe
from conftest import TEMPLATE_1076


@pytest.fixture
def entry():
    return oe.load_osdm(TEMPLATE_1076.read_bytes())


def _fs(entry):
    return entry["data"]["fareDelivery"]["fareStructure"]


def test_list_conditions_1076(entry):
    c = oe.list_conditions(entry)
    sales = c["sales"][0]
    assert sales["bundles"] == ["1076_7.0_S__1", "1076_7.0_S__2"]
    assert sales["restrictions"][0]["start"] == {"timeUnit": "DAYS", "timeValue": 182, "timeReference": "BEFORE_START_VALIDITY"}
    travel = c["travel"][0]
    assert (travel["value"], travel["numberOfTravelDays"], travel["validityType"]) == (2.0, 2, "SINGLE_TRIP")
    groups = {g["id"]: g for g in c["groups"]}
    assert groups["1076_7.0_T__2"]["minWeightedPassengers"] == 10.0
    assert "Voksen (G__2)" in groups["1076_7.0_T__2"]["categories"]


def test_senior_with_zero_weight_is_flagged(entry):
    w = oe.condition_warnings(entry)
    assert [(x["params"]["pc"], x["params"]["bundle"], x["params"]["max"]) for x in w] == [("Senior (G__8)", "S__1", "9")]
    # Småbarn (vekt 0) og hund er bevisst ikke med
    oe.update_passenger(entry, "1076_7.0_G__8", {**{k: v for k, v in next(
        p for p in oe.passenger_details(entry) if p["id"] == "1076_7.0_G__8").items()
        if k not in ("fare_count", "bundles", "siblings", "id")}, "passengerWeight": 1})
    assert oe.condition_warnings(entry) == []


def test_update_sales(entry):
    oe.update_sales(entry, "1076_7.0_M__1", [{
        "start": {"timeValue": "90", "timeUnit": "DAYS", "timeReference": "BEFORE_START_VALIDITY"},
        "end": {"timeValue": "2", "timeUnit": "hours", "timeReference": "AFTER_END_VALIDITY"}}])
    r = _fs(entry)["salesAvailabilityConstraint"][0]["salesRestrictions"][0]
    assert r["startOfSale"]["timeValue"] == 90
    assert r["endOfSale"] == {"timeUnit": "HOURS", "timeValue": 2, "timeReference": "AFTER_END_VALIDITY"}
    assert r["salesDatesRef"] == "1076_7.0_B__1"


def test_update_travel_keeps_number_types(entry):
    oe.update_travel(entry, "1076_7.0_Q__1", {"value": "3", "timeUnit": "DAYS", "hoursAfterMidnight": "4",
                                              "numberOfTravelDays": "3", "validityType": "MULTIPLE_TRIPS"})
    c = _fs(entry)["travelValidityConstraints"][0]
    assert c["validityRange"] == {"timeUnit": "DAYS", "value": 3.0, "hoursAfterMidnight": 4.0}
    assert isinstance(c["validityRange"]["value"], float)  # 1076 har 2.0
    assert c["numberOfTravelDays"] == 3 and isinstance(c["numberOfTravelDays"], int)
    assert c["validityType"] == "MULTIPLE_TRIPS"
    assert c["excludedTimeRange"] == []


def test_update_group(entry):
    oe.update_group(entry, "1076_7.0_T__2", "8", "40.5")
    g = next(x for x in _fs(entry)["passengerCombinationConstraints"] if x["id"] == "1076_7.0_T__2")
    assert (g["minWeightedPassengers"], g["maxWeightedPassengers"]) == (8.0, 40.5)


@pytest.mark.parametrize("call,err", [
    (lambda e: oe.update_group(e, "1076_7.0_T__1", 5, 2), "cond_group_order"),
    (lambda e: oe.update_group(e, "1076_7.0_T__1", "x", 2), "cond_number_invalid"),
    (lambda e: oe.update_group(e, "finnes_ikke", 1, 2), "cond_not_found"),
    (lambda e: oe.update_travel(e, "1076_7.0_Q__1", {"value": "0", "timeUnit": "DAYS", "numberOfTravelDays": 1,
                                                     "validityType": "SINGLE_TRIP"}), "cond_number_invalid"),
    (lambda e: oe.update_travel(e, "1076_7.0_Q__1", {"value": "2", "timeUnit": "WEEKS", "numberOfTravelDays": 1,
                                                     "validityType": "SINGLE_TRIP"}), "cond_unit_invalid"),
    (lambda e: oe.update_travel(e, "1076_7.0_Q__1", {"value": "2", "timeUnit": "DAYS", "numberOfTravelDays": 1,
                                                     "validityType": "RETURN"}), "cond_validity_type_invalid"),
    (lambda e: oe.update_sales(e, "1076_7.0_M__1", [{"start": {"timeValue": 1, "timeUnit": "DAYS",
                                                               "timeReference": "SOMETIME"},
                                                     "end": {"timeValue": 1, "timeUnit": "DAYS",
                                                             "timeReference": "AFTER_END_VALIDITY"}}]),
     "cond_reference_invalid"),
    (lambda e: oe.update_sales(e, "1076_7.0_M__1", []), "invalid_input"),
])
def test_rejects_bad_input(entry, call, err):
    with pytest.raises(oe.EditorError) as e:
        call(entry)
    assert e.value.code == err


def test_sales_error_changes_nothing(entry):
    before = json.dumps(_fs(entry)["salesAvailabilityConstraint"])
    with pytest.raises(oe.EditorError):
        oe.update_sales(entry, "1076_7.0_M__1", [{
            "start": {"timeValue": "90", "timeUnit": "DAYS", "timeReference": "BEFORE_START_VALIDITY"},
            "end": {"timeValue": "-1", "timeUnit": "DAYS", "timeReference": "AFTER_END_VALIDITY"}}])
    assert json.dumps(_fs(entry)["salesAvailabilityConstraint"]) == before


def test_condition_endpoints():
    from test_api import _login
    client = _login("a@test.no")
    client.post("/osdm-editor/load", files={"osdmFile": ("1076.json", TEMPLATE_1076.read_bytes())})
    data = client.get("/osdm-editor/conditions").json()
    assert data["warnings"][0]["code"] == "w_group_zero_weight" and "message" in data["warnings"][0]
    assert data["time_units"] == ["DAYS", "HOURS", "MINUTES"]

    r = client.post("/osdm-editor/conditions/group/1076_7.0_T__1",
                    json={"minWeightedPassengers": 1, "maxWeightedPassengers": 12})
    assert r.status_code == 200
    r = client.post("/osdm-editor/conditions/group/1076_7.0_T__1",
                    json={"minWeightedPassengers": 5, "maxWeightedPassengers": 1})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "cond_group_order"
    assert client.post("/osdm-editor/conditions/group/x", json={}).status_code == 404
    assert client.post("/osdm-editor/conditions/foo/x", json={}).status_code == 422

    out = json.loads(client.get("/osdm-editor/download").content)["fareDelivery"]["fareStructure"]
    assert out["passengerCombinationConstraints"][0]["maxWeightedPassengers"] == 12.0
