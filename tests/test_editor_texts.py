"""Tekster og stasjonsnavn i OSDM-editoren."""
import json

import pytest

from backend import osdm_editor as oe
from conftest import TEMPLATE_1076


@pytest.fixture
def entry():
    return oe.load_osdm(TEMPLATE_1076.read_bytes())


def _fs(entry):
    return entry["data"]["fareDelivery"]["fareStructure"]


def _text(entry, tid):
    return next(t for t in _fs(entry)["texts"] if t["id"] == tid)


def test_ascii_fold():
    assert oe.ascii_fold("Meråker") == "Meraaker"
    assert oe.ascii_fold("Barn 6-17 år") == "Barn 6-17 aar"
    assert oe.ascii_fold("Malmö Göteborg Ærø") == "Malmoe Goeteborg Aeroe"
    assert oe.ascii_fold("Genève Zürich") == "Geneve Zuerich"


def test_list_texts_shows_usage(entry):
    texts = {t["id"]: t for t in oe.list_texts(entry)}
    voksen = texts["1076_7.0_P__7"]
    assert voksen["text"] == "Voksen" and voksen["legacy_ascii"] is True
    used = {u["where"]: u["count"] for u in voksen["used_by"]}
    assert used["fares.nameRef"] == 1166
    assert used["passengerConstraints.nameRef"] == 2
    assert {u["where"] for u in texts["1076_7.0_P__6"]["used_by"]} == {"serviceClassDefinitions.textRef"}
    # Barn: UTF-8 med «år», ASCII-feltet har engelsk
    barn = texts["1076_7.0_P__8"]
    assert barn["text"] == "Barn 6-17 år" and barn["text_ascii"] == "Barn 6-17 year"


def test_update_text_fills_ascii_and_short(entry):
    oe.update_text(entry, "1076_7.0_P__5", {"text": "FIP-rabatt for fritidsreiser", "shortText": "",
                                             "textAscii": "", "translations": [
                                                 {"language": "EN", "text": "FIP leisure reduction"},
                                                 {"language": "", "text": ""}]})
    t = _text(entry, "1076_7.0_P__5")
    assert t["textUtf8"] == "FIP-rabatt for fritidsreiser"
    assert t["shortTextUtf8"] == t["textUtf8"]
    assert t["text"] == "FIP-rabatt for fritidsreiser"
    assert t["translations"] == [{"language": "en", "text": "FIP leisure reduction"}]


def test_update_text_keeps_given_ascii_and_refreshes_names(entry):
    oe.update_text(entry, "1076_7.0_P__7", {"text": "Voksen 18–66 år", "textAscii": "Adult 18-66"})
    t = _text(entry, "1076_7.0_P__7")
    assert t["text"] == "Adult 18-66" and t["textUtf8"] == "Voksen 18–66 år"
    assert t["shortText"] == "Voksen 18-66 aar"  # ASCII av korttekst (= teksten)
    summary = oe.get_summary(entry)
    assert any(p["name"] == "Voksen 18–66 år" for p in summary["passenger_types"])


def test_update_text_7_0_format_has_no_utf8_fields():
    entry = {"data": {"fareDelivery": {"fareStructure": {"texts": [{"id": "X", "text": "a"}]}}}}
    oe.update_text(entry, "X", {"text": "Ærlig"})
    assert entry["data"]["fareDelivery"]["fareStructure"]["texts"][0] == {
        "id": "X", "text": "Ærlig", "shortText": "Ærlig", "translations": []}


@pytest.mark.parametrize("fields,err", [
    ({"text": "  "}, "text_empty"),
    ({"text": "x", "translations": [{"language": "eng", "text": "x"}]}, "text_language_invalid"),
    ({"text": "x", "translations": [{"language": "en", "text": ""}]}, "text_translation_empty"),
    ({"text": "x", "translations": [{"language": "en", "text": "a"}, {"language": "en", "text": "b"}]},
     "text_language_duplicate"),
])
def test_update_text_rejects_bad_input(entry, fields, err):
    with pytest.raises(oe.EditorError) as e:
        oe.update_text(entry, "1076_7.0_P__7", fields)
    assert e.value.code == err


def test_station_list_finds_missing_names(entry):
    res = oe.list_station_names(entry, missing_only=True)
    assert {s["uic"] for s in res["stations"]} == {"7403787", "7400731"}
    assert res["missing_count"] == 2
    found = oe.list_station_names(entry, "kornsj")["stations"]
    assert [(s["uic"], s["border_code"]) for s in found] == [("7600551", "543")]


def test_add_and_update_station_name(entry):
    assert oe.update_station_name(entry, "7400731", "Kornsjö (Gr)")["created"] is True
    sn = next(s for s in _fs(entry)["stationNames"] if s["code"] == "7400731")
    assert sn["nameUtf8"] == "Kornsjö (Gr)" and sn["name"] == "Kornsjoe (Gr)"
    assert sn["shortNameUtf8"] == "Kornsjö (Gr)"
    assert entry["station_name_map"]["7400731"] == "Kornsjö (Gr)"

    oe.update_station_name(entry, "7601214", "Meråker")
    sn = next(s for s in _fs(entry)["stationNames"] if s["code"] == "7601214")
    assert (sn["name"], sn["nameUtf8"], sn["shortName"]) == ("Meraaker", "Meråker", "Meraaker")  # kortnavn urørt

    with pytest.raises(oe.EditorError):
        oe.update_station_name(entry, "760", "x")
    with pytest.raises(oe.EditorError):
        oe.update_station_name(entry, "7601214", " ")


def test_text_and_station_endpoints():
    from test_api import _login
    client = _login("a@test.no")
    client.post("/osdm-editor/load", files={"osdmFile": ("1076.json", TEMPLATE_1076.read_bytes())})
    assert len(client.get("/osdm-editor/texts").json()["texts"]) == 10
    r = client.post("/osdm-editor/text/1076_7.0_P__11", json={"text": "Honnør"})
    assert r.status_code == 200
    assert client.post("/osdm-editor/text/finnes_ikke", json={"text": "x"}).status_code == 404
    r = client.post("/osdm-editor/text/1076_7.0_P__11", json={"text": ""})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "text_empty"

    st = client.get("/osdm-editor/stations", params={"missing": "true"}).json()
    assert st["missing_count"] == 2
    assert client.post("/osdm-editor/station/7403787", json={"name": "Storlien"}).json()["created"] is True

    out = json.loads(client.get("/osdm-editor/download").content)["fareDelivery"]["fareStructure"]
    assert next(t for t in out["texts"] if t["id"] == "1076_7.0_P__11")["text"] == "Honnoer"
    assert any(s["code"] == "7403787" and s["name"] == "Storlien" for s in out["stationNames"])
