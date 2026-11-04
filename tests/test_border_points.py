"""Grensepunkter i OSDM-editoren (connectionPoints med legacyBorderPointCode ≠ 0)."""
import json

import pytest

from backend import osdm_editor as oe
from conftest import TEMPLATE_1076


@pytest.fixture
def entry():
    return oe.load_osdm(TEMPLATE_1076.read_bytes())


def _fs(entry):
    return entry["data"]["fareDelivery"]["fareStructure"]


def _bp(entry, code):
    return next(b for b in oe.list_border_points(entry) if b["code"] == code)


def test_lists_border_points_with_sides_and_relations(entry):
    points = oe.list_border_points(entry)
    assert [b["code"] for b in points] == ["541", "543"]
    kornsjo = _bp(entry, "543")
    assert kornsjo["id"] == "1076_7.0_E__56"
    assert [[s["uic"] for s in side] for side in kornsjo["sides"]] == [["7600551"], ["7400731"]]
    assert kornsjo["sides"][0][0]["name"] == "Kornsjoe(Gr)"
    assert kornsjo["rc_count"] == 170


def test_flags_storlien_meraker_mismatch(entry):
    """1076: punkt 541 har Meråker, men rutene og stasjonsnavnet med kode 541 er Storlien."""
    codes = {w["code"]: w["params"] for w in _bp(entry, "541")["warnings"]}
    assert "7601218" in codes["bp_route_mismatch"]["stations"]
    assert "7601218" in codes["bp_code_elsewhere"]["stations"]
    assert _bp(entry, "543")["warnings"] == []


def test_update_fixes_storlien_and_adds_swedish_name(entry):
    oe.update_border_point(entry, "1076_7.0_E__37", "541", [["7601218"], ["7403787"]],
                           {"7403787": "Storlien"})
    bp = _bp(entry, "541")
    assert bp["warnings"] == []
    assert bp["sides"][1][0]["name"] == "Storlien"
    # Ny stasjon slås opp til grensepunktet
    assert entry["uic_to_cp"]["7601218"] == "1076_7.0_E__37"
    # Meråker har også sitt eget vanlige punkt; før rettingen pekte den på grensepunkt 541
    assert entry["uic_to_cp"]["7601214"] == "1076_7.0_E__63"


def test_changing_code_updates_station_names(entry):
    oe.update_border_point(entry, "1076_7.0_E__56", "544", [["7600551"], ["7400731"]])
    sn = next(s for s in _fs(entry)["stationNames"] if s["code"] == "7600551")
    assert sn["legacyBorderPointCode"] == 544
    cp = next(c for c in _fs(entry)["connectionPoints"] if c["id"] == "1076_7.0_E__56")
    assert cp["legacyBorderPointCode"] == "544"  # streng i connectionPoints, tall i stationNames


@pytest.mark.parametrize("code,sides,err", [
    ("0", [["7600551"]], "bp_code_invalid"),
    ("abc", [["7600551"]], "bp_code_invalid"),
    ("541", [["7600551"]], "bp_code_in_use"),
    ("543", [["123"]], "bp_uic_invalid"),
    ("543", [["7600551"], ["7600551"]], "bp_uic_duplicate"),
    ("543", [], "bp_sides_invalid"),
    ("543", [["7600551"], ["7602317"]], "bp_uic_in_use"),  # Aal ligger i et annet punkt
])
def test_update_rejects_bad_input(entry, code, sides, err):
    with pytest.raises(oe.BorderPointError) as e:
        oe.update_border_point(entry, "1076_7.0_E__56", code, sides)
    assert e.value.code == err


def test_add_converts_existing_regular_point(entry):
    """Stasjon som allerede er et vanlig punkt (Aal) beholder relasjonene sine."""
    cp_id = entry["uic_to_cp"]["7602317"]
    res = oe.add_border_point(entry, "999", [["7602317"], ["7499999"]])
    assert res == {"id": cp_id, "code": "999", "converted": True}
    assert _bp(entry, "999")["rc_count"] > 0
    sn = next(s for s in _fs(entry)["stationNames"] if s["code"] == "7602317")
    assert sn["legacyBorderPointCode"] == 999


def test_new_point_id_does_not_collide_with_fulfillment_constraint(entry):
    """E-prefikset deles med fulfillmentConstraints (1076_7.0_E__150)."""
    res = oe.add_border_point(entry, "998", [["7699998"], ["7499998"]], {"7699998": "Testgrense"})
    assert res["converted"] is False
    taken = {c["id"] for c in _fs(entry)["fulfillmentConstraints"]}
    assert res["id"] not in taken
    assert res["id"] == "1076_7.0_E__151"
    assert entry["station_name_map"]["7699998"] == "Testgrense"


def test_add_relation_new_cp_avoids_fulfillment_id(entry):
    res = oe.add_relation(entry, "7600100", "7699997", 50.0, 100)
    rc = next(r for r in _fs(entry)["regionalConstraints"] if r["id"] == res["rc_id"])
    assert rc["exitConnectionPointId"] != "1076_7.0_E__150"


def test_add_relation_to_border_station_reuses_border_point(entry):
    """Storlien(Gr) 7601218 har kode 541 → relasjonen skal bruke grensepunkt 541, ikke nytt punkt."""
    n_cps = len(_fs(entry)["connectionPoints"])
    res = oe.add_relation(entry, "7600100", "7601218", 50.0, 100)
    rc = next(r for r in _fs(entry)["regionalConstraints"] if r["id"] == res["rc_id"])
    assert rc["exitConnectionPointId"] == "1076_7.0_E__37"
    assert len(_fs(entry)["connectionPoints"]) == n_cps  # Oslo S finnes fra før


def test_trenitalia_station_name_format():
    """0083 bruker country+localCode i stationNames – nye navn skal følge samme format."""
    fs = {"stationNames": [{"country": 83, "localCode": 11705, "name": "Agropoli C."}]}
    oe._set_station_names(fs, {"8775698": "Breil-sur-Roya"})
    assert fs["stationNames"][-1]["country"] == 87
    assert fs["stationNames"][-1]["localCode"] == 75698
    assert oe._station_names_by_uic(fs["stationNames"])["8775698"]["name"] == "Breil-sur-Roya"


def test_border_point_endpoints():
    from test_api import _login
    client = _login("a@test.no")
    client.post("/osdm-editor/load", files={"osdmFile": ("1076.json", TEMPLATE_1076.read_bytes())})
    points = client.get("/osdm-editor/border-points").json()["border_points"]
    storlien = next(b for b in points if b["code"] == "541")
    assert all("message" in w for w in storlien["warnings"])  # oversettbare advarsler

    r = client.post("/osdm-editor/border-point/1076_7.0_E__37",
                    json={"code": "541", "sides": [["7601218"], ["7403787"]], "names": {"7403787": "Storlien"}})
    assert r.status_code == 200
    r = client.post("/osdm-editor/border-point", json={"code": "543", "sides": [["7699996"]]})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bp_code_in_use"
    r = client.post("/osdm-editor/border-point/finnes_ikke", json={"code": "1", "sides": [["7699996"]]})
    assert r.status_code == 404
    r = client.post("/osdm-editor/border-point", json={"code": "1", "sides": "x"})
    assert r.status_code == 422

    out = json.loads(client.get("/osdm-editor/download").content)
    cp = next(c for c in out["fareDelivery"]["fareStructure"]["connectionPoints"] if c["id"] == "1076_7.0_E__37")
    assert cp["stationSets"][0][0]["code"] == "7601218"
