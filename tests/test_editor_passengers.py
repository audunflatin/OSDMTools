"""Passasjerkategorier i OSDM-editoren: aldersgrenser, vekt, kombinasjonsregler og kontroll."""
import json

import pytest

from backend import osdm_editor as oe
from conftest import TEMPLATE_1076

ADULT, ADULT_GROUP, CHILD, DOG = "1076_7.0_G__1", "1076_7.0_G__2", "1076_7.0_G__3", "1076_7.0_G__5"


@pytest.fixture
def entry():
    return oe.load_osdm(TEMPLATE_1076.read_bytes())


def _pc(entry, pc_id):
    return next(p for p in entry["data"]["fareDelivery"]["fareStructure"]["passengerConstraints"] if p["id"] == pc_id)


def _fields(entry, pc_id, **changes):
    d = next(p for p in oe.passenger_details(entry) if p["id"] == pc_id)
    fields = {k: d[k] for k in ("nameRef", "passengerType", "lowerAgeLimit", "upperAgeLimit",
                                "ageLimitToTravelAlone", "ageLimitForReservation", "passengerWeight",
                                "isAncillaryItem", "combinationConstraint")}
    fields.update(changes)
    return fields


def test_details_show_usage_and_siblings(entry):
    d = {p["id"]: p for p in oe.passenger_details(entry)}
    assert d[ADULT]["siblings"] == [ADULT_GROUP]
    assert d[ADULT]["bundles"] == ["1076_7.0_S__1"] and d[ADULT_GROUP]["bundles"] == ["1076_7.0_S__2"]
    assert d[ADULT]["fare_count"] == 2332  # inkl. FIP-farer
    assert d[DOG]["combinationConstraint"] == [{"passengerConstraintRef": ADULT, "minNumber": 1, "maxNumber": 999}]


def test_1076_has_no_warnings(entry):
    assert oe.passenger_warnings(entry) == []


def test_update_ages_also_on_siblings(entry):
    res = oe.update_passenger(entry, ADULT, _fields(entry, ADULT, lowerAgeLimit=20), [ADULT_GROUP])
    assert res["updated"] == [ADULT, ADULT_GROUP]
    assert _pc(entry, ADULT)["lowerAgeLimit"] == 20
    assert _pc(entry, ADULT_GROUP)["lowerAgeLimit"] == 20
    # Hull mellom barn (til 17) og voksen (fra 20) i begge bundles
    gaps = [w for w in oe.passenger_warnings(entry) if w["code"] == "w_pc_age_gap"]
    assert {(w["params"]["bundle"], w["params"]["from"], w["params"]["to"]) for w in gaps} == {
        ("S__1", 18, 19), ("S__2", 18, 19)}


def test_update_without_siblings_and_overlap_warning(entry):
    oe.update_passenger(entry, CHILD, _fields(entry, CHILD, upperAgeLimit="19"))
    assert _pc(entry, CHILD)["upperAgeLimit"] == 19
    assert _pc(entry, "1076_7.0_G__4")["upperAgeLimit"] == 17
    w = oe.passenger_warnings(entry)
    assert [x["code"] for x in w] == ["w_pc_age_overlap"]
    assert w[0]["params"]["bundle"] == "S__1" and "18–66" in w[0]["params"]["b"]


def test_update_weight_type_name_and_combinations(entry):
    fields = _fields(entry, DOG, passengerType="companion_dog", passengerWeight="0.5",
                     nameRef="1076_7.0_P__11",
                     combinationConstraint=[{"passengerConstraintRef": ADULT, "minNumber": "1", "maxNumber": "2"},
                                            {"passengerConstraintRef": "1076_7.0_G__8", "minNumber": 0, "maxNumber": 2}])
    oe.update_passenger(entry, DOG, fields)
    pc = _pc(entry, DOG)
    assert pc["passengerType"] == "COMPANION_DOG"
    assert pc["passengerWeight"] == 0.5
    assert pc["nameRef"] == "1076_7.0_P__11"
    assert pc["combinationConstraint"][0] == {"maxNumber": 2, "minNumber": 1, "passengerConstraintRef": ADULT}
    # Ratio og priser røres ikke
    assert pc["id"] in entry["ratios"]


def test_dog_is_not_checked_for_age_overlap(entry):
    # Hund 0–100 overlapper alle personkategorier, men er ikke en person
    assert not any(w["code"] == "w_pc_age_overlap" for w in oe.passenger_warnings(entry))


@pytest.mark.parametrize("changes,err", [
    ({"passengerType": "voksen 1"}, "pc_type_invalid"),
    ({"lowerAgeLimit": "abc"}, "pc_age_invalid"),
    ({"lowerAgeLimit": "6.5"}, "pc_age_invalid"),
    ({"upperAgeLimit": -1}, "pc_age_invalid"),
    ({"lowerAgeLimit": 70}, "pc_age_order"),
    ({"passengerWeight": "x"}, "pc_weight_invalid"),
    ({"passengerWeight": 101}, "pc_weight_invalid"),
    ({"nameRef": "finnes_ikke"}, "pc_name_ref_unknown"),
    ({"combinationConstraint": [{"passengerConstraintRef": "x", "minNumber": 0, "maxNumber": 1}]}, "pc_comb_ref_unknown"),
    ({"combinationConstraint": [{"passengerConstraintRef": DOG, "minNumber": 0, "maxNumber": 1},
                                {"passengerConstraintRef": DOG, "minNumber": 0, "maxNumber": 2}]}, "pc_comb_duplicate"),
    ({"combinationConstraint": [{"passengerConstraintRef": DOG, "minNumber": 3, "maxNumber": 1}]}, "pc_comb_invalid"),
])
def test_update_rejects_bad_input(entry, changes, err):
    with pytest.raises(oe.EditorError) as e:
        oe.update_passenger(entry, ADULT, _fields(entry, ADULT, **changes))
    assert e.value.code == err
    assert _pc(entry, ADULT)["lowerAgeLimit"] == 18  # ingenting endret ved feil


def test_update_rejects_non_sibling(entry):
    with pytest.raises(oe.EditorError) as e:
        oe.update_passenger(entry, ADULT, _fields(entry, ADULT), [CHILD])
    assert e.value.code == "pc_not_sibling"


def test_passenger_endpoints():
    from test_api import _login
    client = _login("a@test.no")
    client.post("/osdm-editor/load", files={"osdmFile": ("1076.json", TEMPLATE_1076.read_bytes())})
    data = client.get("/osdm-editor/passengers").json()
    adult = next(p for p in data["passengers"] if p["id"] == ADULT)
    assert adult["is_adult"] and adult["name"] == "Voksen" and adult["ratio"] == 1.0
    assert data["warnings"] == [] and "ADULT" in data["types"] and len(data["texts"]) == 10

    body = {k: adult[k] for k in ("nameRef", "passengerType", "lowerAgeLimit", "upperAgeLimit",
                                  "ageLimitToTravelAlone", "ageLimitForReservation", "passengerWeight",
                                  "isAncillaryItem", "combinationConstraint")}
    body.update(upperAgeLimit=64, also_ids=[ADULT_GROUP])
    r = client.post(f"/osdm-editor/passenger/{ADULT}/details", json=body)
    assert r.status_code == 200 and r.json()["updated"] == [ADULT, ADULT_GROUP]
    warnings = client.get("/osdm-editor/passengers").json()["warnings"]
    assert warnings and all("message" in w for w in warnings)  # hull 65–66 før senior (67)

    r = client.post(f"/osdm-editor/passenger/{ADULT}/details", json={**body, "lowerAgeLimit": 99})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "pc_age_order"
    assert client.post("/osdm-editor/passenger/x/details", json=body).status_code == 404

    out = json.loads(client.get("/osdm-editor/download").content)["fareDelivery"]["fareStructure"]
    assert next(p for p in out["passengerConstraints"] if p["id"] == ADULT_GROUP)["upperAgeLimit"] == 64
