"""Relasjoner i OSDM-editoren: søk, endre avstand/voksenpris og slette."""
import json

import pytest

from backend import osdm_editor as oe
from conftest import TEMPLATE_1076


@pytest.fixture
def entry():
    return oe.load_osdm(TEMPLATE_1076.read_bytes())


def _fs(entry):
    return entry["data"]["fareDelivery"]["fareStructure"]


def _first_rc(entry):
    return _fs(entry)["regionalConstraints"][0]


def test_list_relations_with_paging(entry):
    res = oe.list_relations(entry, offset=0, limit=50)
    assert res["total"] == res["rc_count"] == 1166
    assert len(res["relations"]) == 50
    rc = res["relations"][0]
    assert rc["fare_count"] == 10 and rc["adult_eur"] > 0
    # Kornsjø er grensepunkt 543
    assert rc["from"]["border"] == "543"
    assert oe.list_relations(entry, offset=1150, limit=50)["relations"][-1]["id"] == _fs(entry)["regionalConstraints"][-1]["id"]


def test_search_matches_all_terms(entry):
    res = oe.list_relations(entry, "kornsj aal")
    assert res["total"] >= 1
    for rc in res["relations"]:
        hay = f'{rc["from"]["name"]} {rc["to"]["name"]}'.lower()
        assert "kornsj" in hay and "aal" in hay
    assert oe.list_relations(entry, "finnesikke")["total"] == 0
    assert oe.list_relations(entry, _first_rc(entry)["id"])["total"] == 1


def test_update_distance_only(entry):
    rc_id = _first_rc(entry)["id"]
    before = oe.adult_prices_by_rc(entry)[rc_id]
    res = oe.update_relation(entry, rc_id, distance="123")
    assert _first_rc(entry)["distance"] == 123 and res["updated_fares"] == 0
    assert oe.adult_prices_by_rc(entry)[rc_id] == before


def test_update_adult_price_uses_ratios(entry):
    rc_id = _first_rc(entry)["id"]
    res = oe.update_relation(entry, rc_id, adult_eur="100,10")
    assert res["adult_after"] == 100.20  # rundet opp til 0,20
    assert res["updated_fares"] == 10
    pm = oe._build_price_map(_fs(entry)["prices"])
    child = max(pm[f["priceRef"]] for f in _fs(entry)["fares"]
                if f["regionalConstraintRef"] == rc_id and f["passengerConstraintRef"] == "1076_7.0_G__3")
    assert child == oe.round_up_020(100.20 * 0.25)
    # Andre relasjoner er urørt
    other = _fs(entry)["regionalConstraints"][1]["id"]
    assert oe.adult_prices_by_rc(entry)[other] == oe.adult_prices_by_rc(oe.load_osdm(TEMPLATE_1076.read_bytes()))[other]


@pytest.mark.parametrize("kwargs,err", [
    ({"distance": "-1"}, "rc_distance_invalid"),
    ({"distance": "12.5"}, "rc_distance_invalid"),
    ({"adult_eur": "0"}, "rc_price_invalid"),
    ({"adult_eur": "abc"}, "rc_price_invalid"),
])
def test_update_rejects_bad_input(entry, kwargs, err):
    with pytest.raises(oe.EditorError) as e:
        oe.update_relation(entry, _first_rc(entry)["id"], **kwargs)
    assert e.value.code == err


def test_delete_removes_fares_and_orphans_only(entry):
    fs = _fs(entry)
    rc = oe.add_relation(entry, "7600100", "7699990", 77.0, 50)  # ny stasjon → nytt punkt
    new_cp = next(r for r in fs["regionalConstraints"] if r["id"] == rc["rc_id"])["exitConnectionPointId"]
    n_prices = len(fs["prices"])
    oslo_cp = entry["uic_to_cp"]["7600100"]

    res = oe.delete_relations(entry, [rc["rc_id"]])
    assert res["deleted_rcs"] == 1 and res["deleted_fares"] == rc["fares_created"]
    assert not any(f["regionalConstraintRef"] == rc["rc_id"] for f in fs["fares"])
    # Ubrukt punkt fjernes, Oslo S (brukt av andre) beholdes
    cp_ids = {cp["id"] for cp in fs["connectionPoints"]}
    assert new_cp not in cp_ids and oslo_cp in cp_ids
    assert res["deleted_cps"] == 1
    # Prisene for 77 EUR (×ratio) var nye og bare brukt av den slettede relasjonen
    assert len(fs["prices"]) == n_prices - res["deleted_prices"]
    used = {f["priceRef"] for f in fs["fares"]}
    assert all(p["id"] in used for p in fs["prices"])


def test_delete_keeps_border_points(entry):
    fs = _fs(entry)
    kornsjo_rcs = [r["id"] for r in fs["regionalConstraints"] if "1076_7.0_E__56" in
                   (r["entryConnectionPointId"], r["exitConnectionPointId"])]
    oe.delete_relations(entry, kornsjo_rcs)
    assert any(cp["id"] == "1076_7.0_E__56" for cp in fs["connectionPoints"])
    assert oe.list_border_points(entry)[1]["rc_count"] == 0


def test_delete_guards(entry):
    with pytest.raises(oe.EditorError) as e:
        oe.delete_relations(entry, ["finnes_ikke"])
    assert e.value.code == "rc_not_found"
    with pytest.raises(oe.EditorError) as e:
        oe.delete_relations(entry, [r["id"] for r in _fs(entry)["regionalConstraints"]])
    assert e.value.code == "rc_delete_all"
    # Referanse fra en annen seksjon
    rc_id = _first_rc(entry)["id"]
    _fs(entry)["reductionConstraints"] = [{"id": "x", "regionalConstraintRef": rc_id}]
    with pytest.raises(oe.EditorError) as e:
        oe.delete_relations(entry, [rc_id])
    assert e.value.code == "rc_in_use"


def test_add_relation_still_works_after_deleting_first(entry):
    first = _first_rc(entry)["id"]
    oe.delete_relations(entry, [first])
    res = oe.add_relation(entry, "7600100", "7602317", 50.0, 100)
    assert res["fares_created"] > 0


def test_relation_endpoints():
    from test_api import _login
    client = _login("a@test.no")
    client.post("/osdm-editor/load", files={"osdmFile": ("1076.json", TEMPLATE_1076.read_bytes())})
    data = client.get("/osdm-editor/relations", params={"limit": 10}).json()
    assert data["total"] == 1166 and len(data["relations"]) == 10
    rc_id = data["relations"][0]["id"]

    r = client.post(f"/osdm-editor/relation/{rc_id}", json={"distance": 200, "adult_price_eur": "55"})
    assert r.status_code == 200 and r.json()["adult_after"] == 55.0
    r = client.post(f"/osdm-editor/relation/{rc_id}", json={"distance": -5})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "rc_distance_invalid"
    assert client.post("/osdm-editor/relation/x", json={"distance": 1}).status_code == 404

    r = client.post("/osdm-editor/relations/delete", json={"ids": [rc_id]})
    assert r.status_code == 200 and r.json()["deleted_fares"] == 10
    assert client.post("/osdm-editor/relations/delete", json={"ids": []}).status_code == 422
    assert client.post("/osdm-editor/relations/delete", json={"ids": [rc_id]}).status_code == 404

    out = json.loads(client.get("/osdm-editor/download").content)["fareDelivery"]["fareStructure"]
    assert len(out["regionalConstraints"]) == 1165
    assert not any(f["regionalConstraintRef"] == rc_id for f in out["fares"])
